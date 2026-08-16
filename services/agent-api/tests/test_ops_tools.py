"""Ops tool inventory API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
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
async def test_ops_tools_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauth = await client.get("/v1/ops/tools")
        assert unauth.status_code == 401

        client.cookies.set("ops_session", token)
        listed = await client.get("/v1/ops/tools")
        assert listed.status_code == 200
        body = listed.json()
        assert "mcp_enabled" in body
        names = {row["name"] for row in body["tools"]}
        assert "web_search" in names
        assert "knowledge_search" in names
        assert all(row["source"] in {"builtin", "mcp"} for row in body["tools"])
