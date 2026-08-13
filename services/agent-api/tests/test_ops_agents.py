"""Ops agent admin API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import Agent
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
