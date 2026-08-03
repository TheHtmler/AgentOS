import httpx
import pytest

from agent_api.tools.search.router import SearchRouter, build_search_router
from agent_api.tools.search.types import SearchProviderError, SearchResponse, SearchResult


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        response: SearchResponse | None = None,
        error: SearchProviderError | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._response = response
        self._error = error
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
    ) -> SearchResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


@pytest.mark.anyio
async def test_router_skips_unavailable_and_uses_next() -> None:
    ddg = FakeProvider(
        "duckduckgo",
        response=SearchResponse(
            provider="duckduckgo",
            query="q",
            results=[SearchResult(title="t", url="https://e.com", snippet="s")],
        ),
    )
    router = SearchRouter(
        [
            FakeProvider("tavily", available=False),
            ddg,
        ]
    )
    result = await router.search("q", max_results=3, timeout=5.0)
    assert result.provider == "duckduckgo"
    assert ddg.calls == 1


@pytest.mark.anyio
async def test_router_failsover_on_recoverable_error() -> None:
    tavily = FakeProvider(
        "tavily",
        error=SearchProviderError("quota", provider="tavily", recoverable=True),
    )
    ddg = FakeProvider(
        "duckduckgo",
        response=SearchResponse(provider="duckduckgo", query="q", results=[]),
    )
    router = SearchRouter([tavily, ddg])
    result = await router.search("q", max_results=3, timeout=5.0)
    assert result.provider == "duckduckgo"
    assert tavily.calls == 1
    assert ddg.calls == 1


@pytest.mark.anyio
async def test_router_does_not_failover_on_empty_results() -> None:
    tavily = FakeProvider(
        "tavily",
        response=SearchResponse(provider="tavily", query="q", results=[]),
    )
    ddg = FakeProvider(
        "duckduckgo",
        response=SearchResponse(
            provider="duckduckgo",
            query="q",
            results=[SearchResult(title="t", url="https://e.com", snippet="s")],
        ),
    )
    router = SearchRouter([tavily, ddg])
    result = await router.search("q", max_results=3, timeout=5.0)
    assert result.provider == "tavily"
    assert ddg.calls == 0


@pytest.mark.anyio
async def test_router_all_providers_fail() -> None:
    router = SearchRouter(
        [
            FakeProvider(
                "tavily",
                error=SearchProviderError("a", provider="tavily", recoverable=True),
            ),
            FakeProvider(
                "duckduckgo",
                error=SearchProviderError("b", provider="duckduckgo", recoverable=True),
            ),
        ]
    )
    with pytest.raises(SearchProviderError, match="all search providers failed") as exc:
        await router.search("q", max_results=3, timeout=5.0)
    assert exc.value.provider == "router"


@pytest.mark.anyio
async def test_router_rejects_blank_query() -> None:
    router = SearchRouter(
        [
            FakeProvider(
                "duckduckgo",
                response=SearchResponse(provider="duckduckgo", query="q", results=[]),
            )
        ]
    )
    with pytest.raises(ValueError, match="query must not be blank"):
        await router.search("   ", max_results=3, timeout=5.0)


def test_build_search_router_ignores_unknown_names() -> None:
    router = build_search_router(
        provider_names=["tavily", "self_hosted", "duckduckgo"],
        tavily_api_key="",
        http_client=httpx.AsyncClient(),
    )
    assert router.provider_names == ["tavily", "duckduckgo"]
