import asyncio
from collections.abc import Callable, Iterator
from typing import Any, Protocol, cast

import ddgs as ddgs_module

from agent_api.tools.search.types import SearchProviderError, SearchResponse, SearchResult


class _DDGSClient(Protocol):
    def text(self, query: str, max_results: int = 5) -> Iterator[dict[str, object]]: ...

    def __enter__(self) -> "_DDGSClient": ...

    def __exit__(self, *args: object) -> None: ...


# ddgs ships without complete type stubs; keep a typed factory for the router.
RawDDGS: Callable[..., _DDGSClient] = cast(
    Callable[..., _DDGSClient],
    ddgs_module.DDGS,  # pyright: ignore[reportUnknownMemberType]
)


def _ddgs_factory() -> _DDGSClient:
    return RawDDGS()


class DuckDuckGoProvider:
    """Free-tier search fallback using the ddgs library."""

    name = "duckduckgo"

    def is_available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
    ) -> SearchResponse:
        try:
            raw_results = await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, max_results),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise SearchProviderError(
                "DuckDuckGo request timed out",
                provider=self.name,
                recoverable=True,
            ) from exc
        except SearchProviderError:
            raise
        except Exception as exc:
            raise SearchProviderError(
                f"DuckDuckGo search failed: {exc}",
                provider=self.name,
                recoverable=True,
            ) from exc

        results: list[SearchResult] = []
        for item in raw_results:
            url_value = str(item.get("href") or item.get("url") or "").strip()
            if not url_value:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url_value,
                    snippet=str(item.get("body") or item.get("snippet") or ""),
                    published_at=None,
                )
            )

        return SearchResponse(provider=self.name, query=query, results=results)

    def _search_sync(self, query: str, max_results: int) -> list[dict[str, Any]]:
        with _ddgs_factory() as client:
            rows = list(client.text(query, max_results=max_results))
        return [cast(dict[str, Any], row) for row in rows]
