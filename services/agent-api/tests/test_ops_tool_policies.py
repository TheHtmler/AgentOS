"""Ops platform tool policy API."""

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
async def test_ops_tool_policies_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauth = await client.get("/v1/ops/tool-policies")
        assert unauth.status_code == 401

        client.cookies.set("ops_session", token)

        listed = await client.get("/v1/ops/tool-policies")
        assert listed.status_code == 200
        rows = listed.json()["tools"]
        assert any(row["name"] == "web_search" for row in rows)
        baseline = next(row for row in rows if row["name"] == "web_search")
        assert baseline["db_action"] is None
        assert baseline["effective_platform_action"] == "allow"

        missing = await client.put(
            "/v1/ops/tool-policies/not_a_real_tool",
            json={"action": "deny"},
        )
        assert missing.status_code == 404

        invalid = await client.put(
            "/v1/ops/tool-policies/web_search",
            json={"action": "allow"},
        )
        assert invalid.status_code == 422

        asked = await client.put(
            "/v1/ops/tool-policies/web_search",
            json={"action": "ask"},
        )
        assert asked.status_code == 200
        assert asked.json()["db_action"] == "ask"
        assert asked.json()["effective_platform_action"] == "ask"

        denied = await client.put(
            "/v1/ops/tool-policies/web_search",
            json={"action": "deny"},
        )
        assert denied.status_code == 200
        assert denied.json()["db_action"] == "deny"

        after_put = await client.get("/v1/ops/tool-policies")
        updated = next(row for row in after_put.json()["tools"] if row["name"] == "web_search")
        assert updated["db_action"] == "deny"
        assert updated["effective_platform_action"] == "deny"

        deleted = await client.delete("/v1/ops/tool-policies/web_search")
        assert deleted.status_code == 204
        deleted_again = await client.delete("/v1/ops/tool-policies/web_search")
        assert deleted_again.status_code == 204

        after_delete = await client.get("/v1/ops/tool-policies")
        restored = next(row for row in after_delete.json()["tools"] if row["name"] == "web_search")
        assert restored["db_action"] is None
        assert restored["effective_platform_action"] == "allow"
