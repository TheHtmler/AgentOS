"""Artifact persist + read_artifact ownership tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from agent_api.config import Settings
from agent_api.db.artifact_store import create_artifact, get_owned_artifact
from agent_api.db.session import close_database, session_factory
from agent_api.tools.artifact.tool import run_read_artifact
from agent_api.tools.fetch.tool import run_fetch_url
from agent_api.tools.fetch.types import FetchResponse
from agent_api.tools.registry import mounted_tool_names
from agent_api.tools.search.tool import AgentDeps


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    """Prevent asyncpg pooled connections from crossing pytest event loops."""

    try:
        yield
    finally:
        await close_database()


class _FakeRouter:
    def __init__(self, text: str) -> None:
        self._text = text

    async def fetch(self, url: str, *, max_chars: int, timeout: float) -> FetchResponse:
        from agent_api.tools.fetch.truncate import apply_fetch_limits

        return apply_fetch_limits(
            provider="local",
            url=url,
            title="Long Page",
            outline="# Intro",
            text=self._text,
            max_chars=max_chars,
        )


def test_read_artifact_is_mounted_when_enabled() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        artifact_enabled=True,
        fetch_url_enabled=False,
        search_enabled=False,
        growth_assess_enabled=False,
        knowledge_search_enabled=False,
        case_context_read_enabled=False,
        mcp_enabled=False,
    )
    names = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        settings=settings,
    )
    assert "read_artifact" in names


def test_read_artifact_hidden_when_disabled() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        artifact_enabled=False,
        fetch_url_enabled=False,
        search_enabled=False,
        growth_assess_enabled=False,
        knowledge_search_enabled=False,
        case_context_read_enabled=False,
        mcp_enabled=False,
    )
    names = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        settings=settings,
    )
    assert "read_artifact" not in names


@pytest.mark.anyio
async def test_fetch_persists_artifact_and_read_window(
    authenticated_api_user: UUID,
) -> None:
    user_id = authenticated_api_user
    body = ("ABCDEFGHIJ" * 200) + "TAIL"
    payload = await run_fetch_url(
        AgentDeps(
            fetch_router=_FakeRouter(body),  # type: ignore[arg-type]
            persist_tool_events=False,
            user_id=user_id,
            thread_id=None,
            run_id=None,
        ),
        "https://example.com/long",
        max_chars=1_000,
    )
    data = json.loads(payload)
    assert data["truncated"] is True
    assert len(data["text"]) == 1_000
    assert "full_text" not in data
    artifact_id = data["artifact_id"]
    assert artifact_id

    first = json.loads(
        await run_read_artifact(
            AgentDeps(persist_tool_events=False, user_id=user_id),
            artifact_id,
            offset=0,
            max_chars=500,
        ),
    )
    assert first["text"] == body[:500]
    assert first["truncated"] is True
    assert first["next_offset"] == 500

    second = json.loads(
        await run_read_artifact(
            AgentDeps(persist_tool_events=False, user_id=user_id),
            artifact_id,
            offset=first["next_offset"],
            max_chars=500,
        ),
    )
    assert second["text"] == body[500:1_000]
    assert second["total_chars"] == len(body)


@pytest.mark.anyio
async def test_read_artifact_denies_other_user(
    authenticated_api_user: UUID,
) -> None:
    owner_id = authenticated_api_user
    async with session_factory() as session, session.begin():
        row = await create_artifact(
            session,
            owner_user_id=owner_id,
            kind="fetch_url",
            title="secret",
            content="private-body",
            source_url="https://example.com/x",
        )
        artifact_id = str(row.id)

    other = json.loads(
        await run_read_artifact(
            AgentDeps(persist_tool_events=False, user_id=uuid4()),
            artifact_id,
        ),
    )
    assert other["code"] == "artifact_not_found"

    async with session_factory() as session:
        missing = await get_owned_artifact(
            session,
            artifact_id=UUID(artifact_id),
            owner_user_id=uuid4(),
        )
        assert missing is None
