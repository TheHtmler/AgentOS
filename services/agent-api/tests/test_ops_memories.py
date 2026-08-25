"""Ops memory admin API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import Agent, User, UserMemory
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


@pytest.mark.anyio
async def test_ops_memories_list_filter_and_delete(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = await database_session.scalar(select(Agent).where(Agent.is_default.is_(True)))
    if agent is None:
        pytest.skip("no default agent seeded in database")

    user = User(email=f"ops-mem-{uuid4().hex}@example.com", status="active")
    other = User(email=f"ops-mem-other-{uuid4().hex}@example.com", status="active")
    database_session.add_all([user, other])
    await database_session.flush()

    profile = UserMemory(
        user_id=user.id,
        agent_id=agent.id,
        kind="profile",
        key="height_cm",
        content="身高 82 cm",
        tags=["身高"],
        embedding=[0.1, 0.2, 0.3],
        embedding_model="test-embed",
        status="active",
    )
    note = UserMemory(
        user_id=user.id,
        agent_id=agent.id,
        kind="note",
        content="对花生过敏",
        tags=["过敏"],
        status="active",
    )
    archived = UserMemory(
        user_id=other.id,
        agent_id=agent.id,
        kind="note",
        content="旧笔记",
        status="archived",
    )
    database_session.add_all([profile, note, archived])
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauth = await client.get("/v1/ops/memories")
        assert unauth.status_code == 401

        client.cookies.set("ops_session", token)

        listed = await client.get("/v1/ops/memories", params={"user_email": user.email})
        assert listed.status_code == 200
        rows = listed.json()["memories"]
        ids = {row["id"] for row in rows}
        assert str(profile.id) in ids
        assert str(note.id) in ids
        assert str(archived.id) not in ids
        match = next(row for row in rows if row["id"] == str(profile.id))
        assert match["user_email"] == user.email
        assert match["agent_name"] == agent.name
        assert match["key"] == "height_cm"
        assert "embedding" not in match
        assert "embedding_model" not in match

        by_kind = await client.get("/v1/ops/memories", params={"kind": "profile"})
        assert by_kind.status_code == 200
        kind_rows = by_kind.json()["memories"]
        assert any(row["id"] == str(profile.id) for row in kind_rows)
        assert all(row["kind"] == "profile" for row in kind_rows)

        by_status = await client.get(
            "/v1/ops/memories",
            params={"status": "archived", "user_email": other.email},
        )
        assert by_status.status_code == 200
        assert [row["id"] for row in by_status.json()["memories"]] == [str(archived.id)]

        by_agent = await client.get("/v1/ops/memories", params={"agent_id": str(agent.id)})
        assert by_agent.status_code == 200
        assert any(row["id"] == str(note.id) for row in by_agent.json()["memories"])

        deleted = await client.delete(f"/v1/ops/memories/{note.id}")
        assert deleted.status_code == 204

        after = await client.get("/v1/ops/memories", params={"user_email": user.email})
        assert str(note.id) not in {row["id"] for row in after.json()["memories"]}

        missing = await client.delete(f"/v1/ops/memories/{uuid4()}")
        assert missing.status_code == 404
