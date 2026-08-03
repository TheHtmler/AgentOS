import json

import pytest

from agent_api.tools.fetch.tool import run_fetch_url
from agent_api.tools.fetch.types import FetchResponse
from agent_api.tools.search.tool import AgentDeps


class _FakeRouter:
    async def fetch(self, url: str, *, max_chars: int, timeout: float) -> FetchResponse:
        return FetchResponse(
            provider="local",
            url=url,
            title="T",
            outline="# H",
            text="body",
            truncated=False,
            total_chars=4,
        )


@pytest.mark.anyio
async def test_run_fetch_url_returns_json() -> None:
    payload = await run_fetch_url(
        AgentDeps(fetch_router=_FakeRouter(), persist_tool_events=False),  # type: ignore[arg-type]
        "https://example.com/a",
    )
    data = json.loads(payload)
    assert data["provider"] == "local"
    assert data["text"] == "body"
    assert data["url"] == "https://example.com/a"


@pytest.mark.anyio
async def test_run_fetch_url_requires_router() -> None:
    payload = await run_fetch_url(AgentDeps(persist_tool_events=False), "https://example.com")
    data = json.loads(payload)
    assert "not configured" in data["error"]


@pytest.mark.anyio
async def test_run_fetch_url_rejects_blank() -> None:
    payload = await run_fetch_url(
        AgentDeps(fetch_router=_FakeRouter(), persist_tool_events=False),  # type: ignore[arg-type]
        "   ",
    )
    data = json.loads(payload)
    assert "blank" in data["error"]
