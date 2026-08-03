import httpx
import pytest

from agent_api.tools.search.tavily import TavilyProvider
from agent_api.tools.search.types import SearchProviderError


@pytest.mark.anyio
async def test_tavily_maps_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "content": "Hello",
                        "published_date": "2026-08-01",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.tavily.com",
    ) as client:
        provider = TavilyProvider(api_key="tvly-test", http_client=client)
        response = await provider.search("q", max_results=5, timeout=5.0)

    assert response.provider == "tavily"
    assert response.results[0].snippet == "Hello"
    assert response.results[0].published_at == "2026-08-01"


@pytest.mark.anyio
async def test_tavily_429_is_recoverable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "quota"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.tavily.com",
    ) as client:
        provider = TavilyProvider(api_key="tvly-test", http_client=client)
        with pytest.raises(SearchProviderError) as exc_info:
            await provider.search("q", max_results=5, timeout=5.0)

    assert exc_info.value.recoverable is True


def test_tavily_unavailable_without_key() -> None:
    provider = TavilyProvider(api_key="  ", http_client=httpx.AsyncClient())
    assert provider.is_available() is False
