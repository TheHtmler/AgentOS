"""Ops console auth: env root login, cookie session, isolation from user cookie."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.session import close_database
from agent_api.main import app

PASSWORD_HASHER = PasswordHash.recommended()


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


def _ops_settings(**updates: object):
    base = get_settings().model_copy(update=dict(updates))
    return base


@pytest.mark.anyio
async def test_ops_login_503_when_password_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _ops_settings(ops_root_password="", ops_root_password_hash="")
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/ops/login",
            json={"username": "admin", "password": "changeme"},
        )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_ops_login_accepts_plaintext_password(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _ops_settings(
        ops_root_username="admin",
        ops_root_password="simple-pass",
        ops_root_password_hash="",
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login = await client.post(
            "/v1/ops/login",
            json={"username": "admin", "password": "simple-pass"},
        )
        assert login.status_code == 200
        client.cookies.set("ops_session", login.json()["session_token"])
        me = await client.get("/v1/ops/me")
        assert me.status_code == 200


@pytest.mark.anyio
async def test_ops_login_401_for_bad_password(monkeypatch: pytest.MonkeyPatch) -> None:
    password_hash = PASSWORD_HASHER.hash("changeme")
    settings = _ops_settings(
        ops_root_username="admin",
        ops_root_password_hash=password_hash,
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/ops/login",
            json={"username": "admin", "password": "wrong"},
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_ops_login_me_and_logout(monkeypatch: pytest.MonkeyPatch) -> None:
    password_hash = PASSWORD_HASHER.hash("changeme")
    settings = _ops_settings(
        ops_root_username="admin",
        ops_root_password_hash=password_hash,
        ops_session_ttl_hours=12,
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login = await client.post(
            "/v1/ops/login",
            json={"username": "admin", "password": "changeme"},
        )
        assert login.status_code == 200
        body = login.json()
        assert body["subject"] == "admin"
        assert body["session_token"]
        client.cookies.set("ops_session", body["session_token"])

        me = await client.get("/v1/ops/me")
        assert me.status_code == 200
        assert me.json()["subject"] == "admin"

        logout = await client.post("/v1/ops/logout")
        assert logout.status_code == 204

        me_after = await client.get("/v1/ops/me")
        assert me_after.status_code == 401


@pytest.mark.anyio
async def test_user_session_cookie_is_ignored_for_ops_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_hash = PASSWORD_HASHER.hash("changeme")
    settings = _ops_settings(
        ops_root_username="admin",
        ops_root_password_hash=password_hash,
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agentos_session", "not-an-ops-token")
        me = await client.get("/v1/ops/me")
        assert me.status_code == 401
