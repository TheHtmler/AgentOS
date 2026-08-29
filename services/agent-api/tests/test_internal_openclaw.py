"""Tests for the deterministic OpenClaw-to-AgentOS binding callback."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import internal_openclaw as internal_openclaw_api
from agent_api.config import get_settings
from agent_api.db.channel_binding_store import issue_channel_binding_invite
from agent_api.db.models import User, UserChannelBinding
from agent_api.db.session import close_database, session_factory
from agent_api.main import app


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_openclaw_binding_and_unbinding_flow(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        email=f"openclaw-binding-{uuid4().hex}@example.com",
        status="active",
    )
    database_session.add(user)
    await database_session.commit()

    settings = get_settings().model_copy(
        update={"openclaw_binding_shared_secret": "test-openclaw-secret"},
    )
    monkeypatch.setattr(internal_openclaw_api, "get_settings", lambda: settings)
    account_id = "582e531a918e-im-bot"
    peer_id = f"wx-user-{uuid4().hex}@im.wechat"
    headers = {"Authorization": "Bearer test-openclaw-secret"}
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauthorized = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            json={"account_id": account_id, "peer_id": peer_id, "text": "绑定机器人"},
        )
        assert unauthorized.status_code == 401

        async with session_factory() as session, session.begin():
            code, _expires_at = await issue_channel_binding_invite(
                session,
                user_id=user.id,
                now=datetime.now(UTC) + timedelta(seconds=1),
            )

        bound = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": peer_id,
                "text": f"绑定{code}",
                "event_id": "bind-complete",
            },
        )
        assert bound.status_code == 200
        assert "绑定成功" in bound.json()["reply"]

        unbind_started = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": peer_id,
                "text": "解绑微信",
                "event_id": "unbind-start",
            },
        )
        assert "确认解绑" in unbind_started.json()["reply"]

        reminder = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": peer_id,
                "text": "不是确认",
                "event_id": "unbind-reminder",
            },
        )
        assert reminder.status_code == 200
        assert "确认解绑" in reminder.json()["reply"]

        unbound = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": peer_id,
                "text": "确认解绑",
                "event_id": "unbind-complete",
            },
        )
        assert unbound.status_code == 200
        assert "解绑成功" in unbound.json()["reply"]

        duplicate = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": peer_id,
                "text": "确认解绑",
                "event_id": "unbind-complete",
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json() == unbound.json()

        prompt_peer_id = f"wx-prompt-{uuid4().hex}@im.wechat"
        prompted = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": prompt_peer_id,
                "text": "绑定微信",
                "event_id": "prompt-start",
            },
        )
        assert prompted.status_code == 200
        assert "生成配对码" in prompted.json()["reply"]

        cancelled = await client.post(
            "/v1/internal/openclaw/weixin/binding-events",
            headers=headers,
            json={
                "account_id": account_id,
                "peer_id": prompt_peer_id,
                "text": "取消",
                "event_id": "prompt-cancel",
            },
        )
        assert cancelled.status_code == 200
        assert "已取消" in cancelled.json()["reply"]

    async with session_factory() as session, session.begin():
        binding = await session.scalar(
            select(UserChannelBinding).where(
                UserChannelBinding.user_id == user.id,
                UserChannelBinding.peer_id == peer_id,
            ),
        )
        assert binding is not None
        assert binding.status == "disabled"
        persisted_user = await session.get(User, user.id)
        if persisted_user is not None:
            await session.delete(persisted_user)
