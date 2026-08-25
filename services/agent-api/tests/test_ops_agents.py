"""Ops agent admin API."""

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
from agent_api.db.models import Agent, AgentVersion
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
async def test_ops_agents_list_and_patch(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents = list(await database_session.scalars(select(Agent).order_by(Agent.slug)))
    if len(agents) < 1:
        pytest.skip("no agents seeded in database")

    default = next((row for row in agents if row.is_default), agents[0])
    other = next((row for row in agents if row.id != default.id), None)

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)

        listed = await client.get("/v1/ops/agents")
        assert listed.status_code == 200
        assert listed.json()["agents"]

        renamed = await client.patch(
            f"/v1/ops/agents/{default.id}",
            json={"description": "ops-managed"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["description"] == "ops-managed"

        blocked = await client.patch(
            f"/v1/ops/agents/{default.id}",
            json={"status": "disabled"},
        )
        assert blocked.status_code == 400

        if other is not None:
            promoted = await client.patch(
                f"/v1/ops/agents/{other.id}",
                json={"is_default": True},
            )
            assert promoted.status_code == 200
            assert promoted.json()["is_default"] is True

            listed_after = await client.get("/v1/ops/agents")
            defaults = [row for row in listed_after.json()["agents"] if row["is_default"]]
            assert len(defaults) == 1
            assert defaults[0]["id"] == str(other.id)


@pytest.mark.anyio
async def test_ops_agent_publish_new_version(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(
        slug=f"ops-ver-{uuid4().hex[:8]}",
        name="Ops version fixture",
        kind="general",
        status="active",
        is_default=False,
    )
    database_session.add(agent)
    await database_session.flush()
    database_session.add(
        AgentVersion(
            agent_id=agent.id,
            version=1,
            system_prompt_overlay="seed-overlay",
            memory_enabled=False,
            case_enabled=False,
            is_published=True,
        ),
    )
    await database_session.commit()
    prior_version = 1

    try:
        token = await _ops_cookie(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set("ops_session", token)

            missing = await client.get("/v1/ops/agents/00000000-0000-0000-0000-000000000000")
            assert missing.status_code == 404

            detail = await client.get(f"/v1/ops/agents/{agent.id}")
            assert detail.status_code == 200
            assert detail.json()["id"] == str(agent.id)

            rejected = await client.post(
                f"/v1/ops/agents/{agent.id}/versions",
                json={"memory_recall_top_k": 0},
            )
            assert rejected.status_code == 422

            published = await client.post(
                f"/v1/ops/agents/{agent.id}/versions",
                json={
                    "system_prompt_overlay": "ops-test-overlay",
                    "memory_enabled": True,
                    "case_enabled": False,
                    "tool_policy_overrides": {"web_search": "ask"},
                    "knowledge_base_slugs": ["mma-pa"],
                    "memory_recall_top_k": 12,
                    "memory_recall_max_chars": 4000,
                    "history_max_runs": 6,
                    "agent_max_requests_per_run": 20,
                },
            )
            assert published.status_code == 200
            body = published.json()
            assert body["published_version"]["system_prompt_overlay"] == "ops-test-overlay"
            assert body["published_version"]["memory_enabled"] is True
            assert body["published_version"]["version"] == prior_version + 1
            assert body["published_version"]["tool_policy_overrides"] == {"web_search": "ask"}
            # Republishing a version must not silently reset scope back to
            # unrestricted just because the field is easy to omit from a payload.
            assert body["published_version"]["knowledge_base_slugs"] == ["mma-pa"]
            assert body["published_version"]["memory_recall_top_k"] == 12
            assert body["published_version"]["memory_recall_max_chars"] == 4000
            assert body["published_version"]["history_max_runs"] == 6
            assert body["published_version"]["agent_max_requests_per_run"] == 20
            published_flags = [row["is_published"] for row in body["versions"]]
            assert published_flags.count(True) == 1
    finally:
        await database_session.rollback()
        persisted = await database_session.get(Agent, agent.id)
        if persisted is not None:
            await database_session.delete(persisted)
            await database_session.commit()
