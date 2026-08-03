import httpx
import pytest

from agent_api.tools.fetch.firecrawl import FirecrawlProvider
from agent_api.tools.fetch.local import LocalFetchProvider
from agent_api.tools.fetch.types import FetchProviderError


@pytest.mark.anyio
async def test_firecrawl_provider_maps_markdown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/scrape"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# Hello\n\nBody text here.",
                    "metadata": {"title": "Hello Page"},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.firecrawl.dev"
    ) as client:
        provider = FirecrawlProvider(api_key="test-key", http_client=client)
        result = await provider.fetch("https://example.com/a", max_chars=10_000, timeout=5.0)

    assert result.provider == "firecrawl"
    assert result.title == "Hello Page"
    assert "Body text" in result.text
    assert "# Hello" in result.outline


@pytest.mark.anyio
async def test_firecrawl_provider_429_is_recoverable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.firecrawl.dev"
    ) as client:
        provider = FirecrawlProvider(api_key="test-key", http_client=client)
        with pytest.raises(FetchProviderError) as exc:
            await provider.fetch("https://example.com/a", max_chars=1000, timeout=5.0)

    assert exc.value.recoverable is True


@pytest.mark.anyio
async def test_local_provider_extracts_html() -> None:
    html = """
    <html><head><title>Local Title</title></head>
    <body><h1>Main</h1><p>Paragraph content for extraction.</p></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://example.com/")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = LocalFetchProvider(http_client=client)
        result = await provider.fetch("https://example.com/post", max_chars=10_000, timeout=5.0)

    assert result.provider == "local"
    assert result.title == "Local Title"
    assert "Paragraph content" in result.text
    assert "# Main" in result.outline
