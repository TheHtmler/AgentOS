import pytest

from agent_api.tools.search.duckduckgo import DuckDuckGoProvider
from agent_api.tools.search.types import SearchProviderError


@pytest.mark.anyio
async def test_duckduckgo_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def __enter__(self) -> "FakeDDGS":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def text(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
            assert query == "agentos"
            assert max_results == 3
            return [
                {
                    "title": "AgentOS",
                    "href": "https://example.com/agentos",
                    "body": "runtime",
                }
            ]

    def fake_ddgs(*_args: object, **_kwargs: object) -> FakeDDGS:
        return FakeDDGS()

    monkeypatch.setattr("agent_api.tools.search.duckduckgo.RawDDGS", fake_ddgs)
    provider = DuckDuckGoProvider()
    response = await provider.search("agentos", max_results=3, timeout=5.0)
    assert response.provider == "duckduckgo"
    assert response.results[0].url == "https://example.com/agentos"
    assert response.results[0].snippet == "runtime"


@pytest.mark.anyio
async def test_duckduckgo_errors_are_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def __enter__(self) -> "FakeDDGS":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def text(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
            raise RuntimeError("blocked")

    def fake_ddgs(*_args: object, **_kwargs: object) -> FakeDDGS:
        return FakeDDGS()

    monkeypatch.setattr("agent_api.tools.search.duckduckgo.RawDDGS", fake_ddgs)
    provider = DuckDuckGoProvider()
    with pytest.raises(SearchProviderError) as exc_info:
        await provider.search("q", max_results=3, timeout=5.0)
    assert exc_info.value.recoverable is True
