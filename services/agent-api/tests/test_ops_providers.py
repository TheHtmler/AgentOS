"""Ops model-provider admin API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import Agent, AgentVersion, ModelProvider
from agent_api.db.ops_store import create_ops_session
from agent_api.db.provider_store import BUILTIN_LOCAL_PROVIDER_ID, sync_builtin_local_provider
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


def _create_payload(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "name": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1/",
        "api_key": "sk-1234567890abcdef",
        "default_model": "deepseek-chat",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "temperature": 0.7,
        "max_concurrent_runs": 4,
        "supports_vision": False,
    }


async def _cleanup_provider(provider_id: str) -> None:
    async with session_factory() as session, session.begin():
        row = await session.get(ModelProvider, UUID(provider_id))
        if row is not None:
            await session.delete(row)


@pytest.mark.anyio
async def test_create_list_and_key_masking(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Ops list always shows the env-managed local row next to remotes.
    await sync_builtin_local_provider(database_session, get_settings())
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    slug = f"deepseek-{uuid4().hex[:8]}"
    provider_id: str | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        try:
            created = await client.post(
                "/v1/ops/model-providers",
                json=_create_payload(slug)
                | {
                    "api_mode": "responses",
                    "reasoning_summary": "concise",
                    "supports_tools": False,
                },
            )
            assert created.status_code == 201
            body = created.json()
            provider_id = body["id"]
            assert body["slug"] == slug
            assert body["api_mode"] == "responses"
            assert body["reasoning_summary"] == "concise"
            assert body["supports_tools"] is False
            # Trailing slash is normalized away.
            assert body["base_url"] == "https://api.deepseek.com/v1"
            assert body["kind"] == "remote"
            assert body["is_builtin"] is False
            # Keys are write-only: masked preview, never the stored value.
            assert body["has_api_key"] is True
            assert body["api_key_preview"] == "sk-...cdef"
            assert "sk-1234567890abcdef" not in created.text
            assert "api_key" not in body

            listed = await client.get("/v1/ops/model-providers")
            assert listed.status_code == 200
            providers = listed.json()["providers"]
            assert providers[0]["slug"] == "local"
            assert providers[0]["is_builtin"] is True
            mine = next(row for row in providers if row["slug"] == slug)
            assert mine["api_key_preview"] == "sk-...cdef"
            assert "sk-1234567890abcdef" not in listed.text
        finally:
            if provider_id is not None:
                await _cleanup_provider(provider_id)


@pytest.mark.anyio
async def test_patch_key_rotation_and_clear(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del database_session
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    slug = f"remote-{uuid4().hex[:8]}"
    provider_id: str | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        try:
            created = await client.post("/v1/ops/model-providers", json=_create_payload(slug))
            provider_id = created.json()["id"]
            # api_mode defaults to chat_completions when omitted.
            assert created.json()["api_mode"] == "chat_completions"
            # supports_tools defaults to true when omitted.
            assert created.json()["supports_tools"] is True

            patched = await client.patch(
                f"/v1/ops/model-providers/{provider_id}",
                json={
                    "name": "代理网关",
                    "supports_vision": True,
                    "supports_tools": False,
                    "enabled": False,
                    "api_mode": "responses",
                    "reasoning_summary": "detailed",
                },
            )
            assert patched.status_code == 200
            assert patched.json()["name"] == "代理网关"
            assert patched.json()["supports_vision"] is True
            assert patched.json()["supports_tools"] is False
            assert patched.json()["enabled"] is False
            assert patched.json()["api_mode"] == "responses"
            assert patched.json()["reasoning_summary"] == "detailed"
            # A patch that omits api_key keeps the stored key.
            assert patched.json()["api_key_preview"] == "sk-...cdef"

            rotated = await client.patch(
                f"/v1/ops/model-providers/{provider_id}",
                json={"api_key": "sk-newkey9999"},
            )
            assert rotated.json()["api_key_preview"] == "sk-...9999"

            cleared = await client.patch(
                f"/v1/ops/model-providers/{provider_id}",
                json={"clear_api_key": True},
            )
            assert cleared.json()["has_api_key"] is False
            assert cleared.json()["api_key_preview"] is None

            empty = await client.patch(f"/v1/ops/model-providers/{provider_id}", json={})
            assert empty.status_code == 400
        finally:
            if provider_id is not None:
                await _cleanup_provider(provider_id)


@pytest.mark.anyio
async def test_builtin_provider_is_read_only(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await sync_builtin_local_provider(database_session, get_settings())
    await database_session.commit()

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        builtin = str(BUILTIN_LOCAL_PROVIDER_ID)

        patched = await client.patch(
            f"/v1/ops/model-providers/{builtin}",
            json={"name": "renamed"},
        )
        assert patched.status_code == 400

        deleted = await client.delete(f"/v1/ops/model-providers/{builtin}")
        assert deleted.status_code == 400

        reserved = await client.post(
            "/v1/ops/model-providers",
            json=_create_payload("local"),
        )
        assert reserved.status_code == 400


@pytest.mark.anyio
async def test_create_validation_and_conflicts(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del database_session
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    slug = f"remote-{uuid4().hex[:8]}"
    provider_id: str | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        try:
            bad_url = await client.post(
                "/v1/ops/model-providers",
                json=_create_payload(f"bad-{uuid4().hex[:8]}") | {"base_url": "ftp://x"},
            )
            assert bad_url.status_code == 422

            bad_slug = await client.post(
                "/v1/ops/model-providers",
                json=_create_payload("Bad_Slug"),
            )
            assert bad_slug.status_code == 422

            created = await client.post("/v1/ops/model-providers", json=_create_payload(slug))
            assert created.status_code == 201
            provider_id = created.json()["id"]

            duplicate = await client.post("/v1/ops/model-providers", json=_create_payload(slug))
            assert duplicate.status_code == 409
        finally:
            if provider_id is not None:
                await _cleanup_provider(provider_id)


@pytest.mark.anyio
async def test_delete_rejected_while_referenced_by_version(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del database_session
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    slug = f"remote-{uuid4().hex[:8]}"
    provider_id: str | None = None
    agent_id: UUID | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        try:
            created = await client.post("/v1/ops/model-providers", json=_create_payload(slug))
            provider_id = created.json()["id"]

            async with session_factory() as session, session.begin():
                agent = Agent(
                    slug=f"prov-ref-{uuid4().hex[:8]}",
                    name="Provider reference fixture",
                    kind="general",
                    status="active",
                    is_default=False,
                )
                session.add(agent)
                await session.flush()
                agent_id = agent.id
                session.add(
                    AgentVersion(
                        agent_id=agent.id,
                        version=1,
                        system_prompt_overlay="",
                        memory_enabled=False,
                        case_enabled=False,
                        is_published=True,
                        model_provider_id=UUID(provider_id),
                    ),
                )

            blocked = await client.delete(f"/v1/ops/model-providers/{provider_id}")
            assert blocked.status_code == 409

            missing = await client.delete(f"/v1/ops/model-providers/{uuid4()}")
            assert missing.status_code == 404
        finally:
            async with session_factory() as session, session.begin():
                if agent_id is not None:
                    agent = await session.get(Agent, agent_id)
                    if agent is not None:
                        await session.delete(agent)
            if provider_id is not None:
                await _cleanup_provider(provider_id)


@pytest.mark.anyio
async def test_publish_version_with_model_provider(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del database_session
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    slug = f"remote-{uuid4().hex[:8]}"
    provider_id: str | None = None
    agent_id: UUID | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        try:
            created = await client.post("/v1/ops/model-providers", json=_create_payload(slug))
            provider_id = created.json()["id"]

            async with session_factory() as session, session.begin():
                agent = Agent(
                    slug=f"prov-pub-{uuid4().hex[:8]}",
                    name="Publish provider fixture",
                    kind="general",
                    status="active",
                    is_default=False,
                )
                session.add(agent)
                await session.flush()
                agent_id = agent.id
                session.add(
                    AgentVersion(
                        agent_id=agent.id,
                        version=1,
                        system_prompt_overlay="",
                        memory_enabled=False,
                        case_enabled=False,
                        is_published=True,
                    ),
                )

            published = await client.post(
                f"/v1/ops/agents/{agent_id}/versions",
                json={"model_provider_id": provider_id},
            )
            assert published.status_code == 200
            assert published.json()["published_version"]["model_provider_id"] == provider_id

            invalid = await client.post(
                f"/v1/ops/agents/{agent_id}/versions",
                json={"model_provider_id": str(uuid4())},
            )
            assert invalid.status_code == 400

            # A disabled provider cannot be published either.
            await client.patch(
                f"/v1/ops/model-providers/{provider_id}",
                json={"enabled": False},
            )
            disabled = await client.post(
                f"/v1/ops/agents/{agent_id}/versions",
                json={"model_provider_id": provider_id},
            )
            assert disabled.status_code == 400
        finally:
            async with session_factory() as session, session.begin():
                if agent_id is not None:
                    agent = await session.get(Agent, agent_id)
                    if agent is not None:
                        await session.delete(agent)
            if provider_id is not None:
                await _cleanup_provider(provider_id)
