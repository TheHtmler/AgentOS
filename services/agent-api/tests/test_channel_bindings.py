"""Tests for the authenticated user's channel pairing controls."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.db.session import close_database
from agent_api.main import app
from agent_api.openclaw_login import WeixinLoginSession


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_authenticated_user_can_issue_pairing_code(
    authenticated_api_user: UUID,
) -> None:
    assert authenticated_api_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        empty = await client.get("/v1/channel-bindings")
        assert empty.status_code == 200
        assert empty.json() == {"bindings": []}

        issued = await client.post("/v1/channel-bindings/pairing-codes")
        assert issued.status_code == 200
        body = issued.json()
        assert body["channel"] == "openclaw-weixin"
        assert len(body["code"]) == 8
        assert body["expires_at"]


@pytest.mark.anyio
async def test_weixin_qr_login_binds_the_scanned_account_to_current_user(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = WeixinLoginSession(
        id=uuid4(),
        user_id=authenticated_api_user,
        adapter_session_id="adapter-session",
        qrcode_url="https://example.com/qr.png",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    async def start(user_id: UUID) -> WeixinLoginSession:
        assert user_id == authenticated_api_user
        return login

    async def status(_: WeixinLoginSession) -> dict[str, object]:
        return {
            "status": "completed",
            "account_id": "scanned-im-bot",
            "peer_id": "scanned@im.wechat",
        }

    async def get(_: UUID, __: UUID) -> WeixinLoginSession:
        return login

    monkeypatch.setattr("agent_api.api.channel_bindings.weixin_login_coordinator.start", start)
    monkeypatch.setattr("agent_api.api.channel_bindings.weixin_login_coordinator.get", get)
    monkeypatch.setattr("agent_api.api.channel_bindings.weixin_login_coordinator.status", status)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = await client.post("/v1/channel-bindings/weixin-login")
        assert started.status_code == 201
        assert started.json()["qrcode_url"] == "https://example.com/qr.png"

        completed = await client.get(f"/v1/channel-bindings/weixin-login/{login.id}")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["binding"]["is_default"] is True

        bindings = await client.get("/v1/channel-bindings")
        assert bindings.status_code == 200
        assert len(bindings.json()["bindings"]) == 1
