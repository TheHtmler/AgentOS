"""Ops session audit API."""

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
from agent_api.db.chat_store import append_model_step_event, append_tool_result_event
from agent_api.db.models import Agent, Message, Run, Thread, User
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
async def test_ops_sessions_list_and_detail(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = await database_session.scalar(select(Agent).where(Agent.is_default.is_(True)))
    if agent is None:
        pytest.skip("no default agent seeded in database")

    user = User(email=f"ops-session-{uuid4().hex}@example.com", status="active")
    thread = Thread(user_id=user.id, agent_id=agent.id, title="ops-audit-thread")
    database_session.add_all([user, thread])
    await database_session.flush()
    database_session.add(
        Message(thread_id=thread.id, seq=1, role="user", content="hello from ops audit"),
    )
    database_session.add(
        Run(thread_id=thread.id, status="completed", model_name="test-model"),
    )
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauth = await client.get("/v1/ops/sessions")
        assert unauth.status_code == 401

        client.cookies.set("ops_session", token)
        listed = await client.get("/v1/ops/sessions", params={"q": "ops-audit-thread"})
        assert listed.status_code == 200
        rows = listed.json()["threads"]
        assert any(row["id"] == str(thread.id) for row in rows)
        match = next(row for row in rows if row["id"] == str(thread.id))
        assert match["user_email"] == user.email
        assert match["last_run_status"] == "completed"
        assert match["message_count"] >= 1

        detail = await client.get(f"/v1/ops/sessions/{thread.id}")
        assert detail.status_code == 200
        assert detail.json()["title"] == "ops-audit-thread"
        assert detail.json()["messages"][0]["content"] == "hello from ops audit"
        assert detail.json()["runs"][0]["status"] == "completed"

        missing = await client.get(f"/v1/ops/sessions/{uuid4()}")
        assert missing.status_code == 404

        other = User(email=f"ops-other-{uuid4().hex}@example.com", status="active")
        other_thread = Thread(user_id=other.id, agent_id=agent.id, title="ops-other-thread")
        database_session.add_all([other, other_thread])
        await database_session.commit()

        filtered = await client.get("/v1/ops/sessions", params={"user_id": str(user.id)})
        assert filtered.status_code == 200
        filtered_ids = {row["id"] for row in filtered.json()["threads"]}
        assert str(thread.id) in filtered_ids
        assert str(other_thread.id) not in filtered_ids
        assert any(item["id"] == str(user.id) for item in filtered.json()["users"])
        assert any(item["email"] == user.email for item in filtered.json()["users"])


@pytest.mark.anyio
async def test_ops_run_events_timeline(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = await database_session.scalar(select(Agent).where(Agent.is_default.is_(True)))
    if agent is None:
        pytest.skip("no default agent seeded in database")

    user = User(email=f"ops-events-{uuid4().hex}@example.com", status="active")
    thread = Thread(user_id=user.id, agent_id=agent.id, title="ops-events-thread")
    database_session.add_all([user, thread])
    await database_session.flush()
    run = Run(thread_id=thread.id, status="completed", model_name="test-model")
    database_session.add(run)
    await database_session.flush()
    await append_tool_result_event(
        database_session,
        run_id=run.id,
        tool_name="knowledge_search",
        provider="postgres",
        ok=True,
        summary="3 hits",
        duration_ms=42,
    )
    await append_model_step_event(
        database_session,
        run_id=run.id,
        duration_ms=1200,
        input_tokens=500,
        output_tokens=80,
    )
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauth = await client.get(f"/v1/ops/sessions/{thread.id}/runs/{run.id}/events")
        assert unauth.status_code == 401

        client.cookies.set("ops_session", token)
        response = await client.get(f"/v1/ops/sessions/{thread.id}/runs/{run.id}/events")
        assert response.status_code == 200
        events = response.json()["events"]
        assert [event["event_type"] for event in events] == ["tool_result", "model_step"]
        assert events[0]["payload"]["duration_ms"] == 42
        assert events[1]["payload"]["input_tokens"] == 500

        other_thread = Thread(user_id=user.id, agent_id=agent.id, title="ops-events-mismatch")
        database_session.add(other_thread)
        await database_session.commit()
        mismatched = await client.get(f"/v1/ops/sessions/{other_thread.id}/runs/{run.id}/events")
        assert mismatched.status_code == 404
