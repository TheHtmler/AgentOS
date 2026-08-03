import httpx
import pytest

from agent_api.tools.fetch.firecrawl import FirecrawlProvider
from agent_api.tools.fetch.local import LocalFetchProvider
from agent_api.tools.fetch.router import FetchRouter, build_fetch_router
from agent_api.tools.fetch.types import FetchProviderError


@pytest.mark.anyio
async def test_router_skips_firecrawl_without_key_and_uses_local() -> None:
    html = (
        "<html><head><title>T</title></head>"
        "<body><h1>H</h1><p>Hello world content.</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "firecrawl" in request.url.host:
            raise AssertionError("firecrawl should be skipped")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        router = build_fetch_router(
            provider_names=["firecrawl", "local"],
            firecrawl_api_key="",
            http_client=client,
        )
        result = await router.fetch("https://example.com/x", max_chars=5000, timeout=5.0)

    assert result.provider == "local"
    assert "Hello world" in result.text


@pytest.mark.anyio
async def test_router_failsover_on_firecrawl_429() -> None:
    html = (
        "<html><head><title>T</title></head>"
        "<body><p>Recovered locally with enough text.</p></body></html>"
    )
    calls = {"firecrawl": 0, "local": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/scrape"):
            calls["firecrawl"] += 1
            return httpx.Response(429, json={"error": "quota"})
        calls["local"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="https://api.firecrawl.dev",
        ) as firecrawl_client,
        httpx.AsyncClient(transport=transport) as local_client,
    ):
        router = FetchRouter(
            [
                FirecrawlProvider(api_key="k", http_client=firecrawl_client),
                LocalFetchProvider(http_client=local_client),
            ]
        )
        result = await router.fetch("https://example.com/x", max_chars=5000, timeout=5.0)

    assert calls["firecrawl"] == 1
    assert calls["local"] == 1
    assert result.provider == "local"


@pytest.mark.anyio
async def test_router_does_not_failover_on_unsafe_url() -> None:
    async with httpx.AsyncClient() as client:
        router = build_fetch_router(
            provider_names=["firecrawl", "local"],
            firecrawl_api_key="k",
            http_client=client,
        )
        with pytest.raises(FetchProviderError) as exc:
            await router.fetch("http://127.0.0.1/", max_chars=1000, timeout=5.0)

    assert exc.value.recoverable is False
