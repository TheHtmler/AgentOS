import json

import pytest

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.tools.search.router import SearchRouter
from agent_api.tools.search.tool import AgentDeps, run_web_search
from agent_api.tools.search.types import SearchProviderError, SearchResponse, SearchResult


class FakeProvider:
    name = "duckduckgo"

    def is_available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
        domains: tuple[str, ...] = (),
    ) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=query,
            results=[
                SearchResult(
                    title="AgentOS",
                    url="https://example.com",
                    snippet="runtime",
                )
            ],
        )


class FailingProvider:
    name = "tavily"

    def is_available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
        domains: tuple[str, ...] = (),
    ) -> SearchResponse:
        raise SearchProviderError("down", provider=self.name, recoverable=True)


@pytest.mark.anyio
async def test_run_web_search_returns_json_results() -> None:
    deps = AgentDeps(
        search_router=SearchRouter([FakeProvider()]),
        persist_tool_events=False,
    )
    payload = json.loads(await run_web_search(deps, "agentos", max_results=3))
    assert payload["provider"] == "duckduckgo"
    assert payload["results"][0]["url"] == "https://example.com"


@pytest.mark.anyio
async def test_run_web_search_returns_error_json() -> None:
    deps = AgentDeps(
        search_router=SearchRouter([FailingProvider()]),
        persist_tool_events=False,
    )
    payload = json.loads(await run_web_search(deps, "agentos"))
    assert "error" in payload
    assert payload["query"] == "agentos"


@pytest.mark.anyio
async def test_run_web_search_normalizes_and_forwards_domains() -> None:
    class ScopedProvider(FakeProvider):
        def __init__(self) -> None:
            self.received_domains: tuple[str, ...] | None = None

        async def search(
            self,
            query: str,
            *,
            max_results: int,
            timeout: float,
            domains: tuple[str, ...] = (),
        ) -> SearchResponse:
            self.received_domains = domains
            return await super().search(
                query,
                max_results=max_results,
                timeout=timeout,
                domains=domains,
            )

    provider = ScopedProvider()
    deps = AgentDeps(search_router=SearchRouter([provider]), persist_tool_events=False)
    payload = json.loads(
        await run_web_search(
            deps,
            "github trending",
            domains=["https://github.com/", "github.com"],
        )
    )

    assert payload["provider"] == "duckduckgo"
    assert provider.received_domains == ("github.com",)


@pytest.mark.anyio
async def test_run_web_search_rejects_non_host_domain() -> None:
    deps = AgentDeps(search_router=SearchRouter([FakeProvider()]), persist_tool_events=False)
    payload = json.loads(await run_web_search(deps, "agentos", domains=["github.com/trending"]))

    assert "invalid search domain" in payload["error"]


@pytest.mark.anyio
async def test_create_agent_registers_web_search_when_enabled() -> None:
    router = SearchRouter([FakeProvider()])
    async with create_ollama_http_client() as http_client:
        enabled = create_agent(
            http_client,
            search_router=router,
            search_enabled=True,
        )
        disabled = create_agent(
            http_client,
            search_router=router,
            search_enabled=False,
        )

    assert "web_search" in _tool_names(enabled)
    assert "web_search" not in _tool_names(disabled)


@pytest.mark.anyio
async def test_create_agent_without_router_skips_tool() -> None:
    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client, search_router=None, search_enabled=True)

    assert "web_search" not in _tool_names(agent)


def _tool_names(agent: object) -> set[str]:
    from typing import cast

    names: set[str] = set()
    toolsets = getattr(agent, "toolsets", ())
    for toolset in toolsets:
        tools = getattr(toolset, "tools", None)
        if not isinstance(tools, dict):
            continue
        for name in cast(dict[object, object], tools):
            names.add(str(name))
    return names
