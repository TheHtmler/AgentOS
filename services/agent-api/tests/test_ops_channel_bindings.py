"""Ops API tests for AgentOS user to channel identity bindings."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import User
from agent_api.db.ops_store import create_ops_session
from agent_api.db.session import close_database, session_factory
from agent_api.main import app

PASSWORD_HASHER = PasswordHash.recommended()


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


async def _ops_cookie(monkeypatch: pytest.MonkeyPatch) -> str:
    settings = get_settings().model_copy(
        update={
            "ops_root_username": "admin",
            "ops_root_password_hash": PASSWORD_HASHER.hash("changeme"),
            "ops_session_ttl_hours": 12,
        },
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        issued = await create_ops_session(
            session,
            subject="admin",
            expires_at=now + timedelta(hours=12),
            now=now,
        )
    return issued.token


def _payload(user_id: str, *, peer_id: str, is_default: bool = False) -> dict[str, object]:
    return {
        "user_id": user_id,
        "channel": "openclaw-weixin",
        "account_id": "582e531a918e-im-bot",
        "peer_id": peer_id,
        "display_name": "微信收件人",
        "receive_notifications": True,
        "allow_openclaw": False,
        "allow_agentos": False,
        "is_default": is_default,
    }


@pytest.mark.anyio
async def test_channel_binding_lifecycle_and_endpoint_conflict(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"binding-{uuid4().hex}@example.com", status="active")
    other = User(email=f"binding-other-{uuid4().hex}@example.com", status="active")
    database_session.add_all([user, other])
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    first_peer_id = f"wx-user-1-{uuid4().hex}"
    second_peer_id = f"wx-user-2-{uuid4().hex}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauthenticated = await client.get("/v1/ops/channel-bindings")
        assert unauthenticated.status_code == 401

        client.cookies.set("ops_session", token)
        users = await client.get("/v1/ops/users", params={"status": "active"})
        assert users.status_code == 200
        assert {row["id"] for row in users.json()["users"]} >= {str(user.id), str(other.id)}

        created = await client.post(
            "/v1/ops/channel-bindings",
            json=_payload(str(user.id), peer_id=first_peer_id),
        )
        assert created.status_code == 201
        first = created.json()
        first_id = first["id"]
        # The first active binding becomes the default even when omitted in the form.
        assert first["is_default"] is True
        assert first["allow_openclaw"] is False
        assert first["allow_agentos"] is False

        conflict = await client.post(
            "/v1/ops/channel-bindings",
            json=_payload(str(other.id), peer_id=first_peer_id),
        )
        assert conflict.status_code == 409

        second = await client.post(
            "/v1/ops/channel-bindings",
            json=_payload(str(user.id), peer_id=second_peer_id, is_default=True),
        )
        assert second.status_code == 201
        second_id = second.json()["id"]
        assert second.json()["is_default"] is True

        listed = await client.get("/v1/ops/channel-bindings")
        assert listed.status_code == 200
        rows = listed.json()["bindings"]
        first_row = next(row for row in rows if row["id"] == first_id)
        assert first_row["is_default"] is False

        disabled = await client.patch(
            f"/v1/ops/channel-bindings/{second_id}",
            json={"status": "disabled"},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"
        assert disabled.json()["is_default"] is False

        removed = await client.delete(f"/v1/ops/channel-bindings/{first_id}")
        assert removed.status_code == 204
        missing = await client.delete(f"/v1/ops/channel-bindings/{first_id}")
        assert missing.status_code == 404

    async with session_factory() as session, session.begin():
        persisted_user = await session.get(User, user.id)
        persisted_other = await session.get(User, other.id)
        if persisted_user is not None:
            await session.delete(persisted_user)
        if persisted_other is not None:
            await session.delete(persisted_other)


@pytest.mark.anyio
async def test_channel_binding_rejects_disabled_users_and_blank_fields(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(email=f"binding-disabled-{uuid4().hex}@example.com", status="disabled")
    database_session.add(user)
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        disabled = await client.post(
            "/v1/ops/channel-bindings",
            json=_payload(str(user.id), peer_id="wx-disabled"),
        )
        assert disabled.status_code == 400

        blank = await client.post(
            "/v1/ops/channel-bindings",
            json=_payload(str(user.id), peer_id="   "),
        )
        assert blank.status_code == 422

    async with session_factory() as session, session.begin():
        persisted = await session.get(User, user.id)
        if persisted is not None:
            await session.delete(persisted)
