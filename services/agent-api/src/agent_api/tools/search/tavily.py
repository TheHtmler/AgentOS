from typing import Any, cast

import httpx

from agent_api.tools.search.types import SearchProviderError, SearchResponse, SearchResult

_DEFAULT_BASE_URL = "https://api.tavily.com"


class TavilyProvider:
    """HTTP adapter for Tavily's OpenAI-oriented search API."""

    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.AsyncClient,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        # Honor an httpx client's base_url (tests inject MockTransport + base_url).
        client_base = str(http_client.base_url) if http_client.base_url else ""
        self._base_url = client_base.rstrip("/") if client_base else base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self._api_key.strip())

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
    ) -> SearchResponse:
        if not self.is_available():
            raise SearchProviderError(
                "Tavily API key is not configured",
                provider=self.name,
                recoverable=True,
            )

        url = f"{self._base_url}/search"
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "search_depth": "basic",
        }

        try:
            response = await self._http_client.post(
                url,
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                "Tavily request timed out",
                provider=self.name,
                recoverable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError(
                f"Tavily transport error: {exc}",
                provider=self.name,
                recoverable=True,
            ) from exc

        if response.status_code in {401, 403, 402, 429} or response.status_code >= 500:
            raise SearchProviderError(
                f"Tavily HTTP {response.status_code}",
                provider=self.name,
                recoverable=True,
            )

        if response.status_code >= 400:
            raise SearchProviderError(
                f"Tavily HTTP {response.status_code}",
                provider=self.name,
                recoverable=True,
            )

        try:
            body = cast(dict[str, Any], response.json())
        except ValueError as exc:
            raise SearchProviderError(
                "Tavily returned invalid JSON",
                provider=self.name,
                recoverable=True,
            ) from exc

        raw_results_obj = body.get("results")
        results: list[SearchResult] = []
        if isinstance(raw_results_obj, list):
            for raw_item in cast(list[object], raw_results_obj):
                if not isinstance(raw_item, dict):
                    continue
                item = cast(dict[str, object], raw_item)
                url_value = str(item.get("url") or "").strip()
                if not url_value:
                    continue
                published = item.get("published_date")
                results.append(
                    SearchResult(
                        title=str(item.get("title") or ""),
                        url=url_value,
                        snippet=str(item.get("content") or ""),
                        published_at=str(published) if published is not None else None,
                    )
                )

        return SearchResponse(provider=self.name, query=query, results=results)
