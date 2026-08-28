import logging

import httpx

from agent_api.tools.search.base import SearchProvider
from agent_api.tools.search.duckduckgo import DuckDuckGoProvider
from agent_api.tools.search.tavily import TavilyProvider
from agent_api.tools.search.types import SearchProviderError, SearchResponse

logger = logging.getLogger(__name__)


class SearchRouter:
    """Try configured search providers in order until one returns useful results."""

    def __init__(self, providers: list[SearchProvider]) -> None:
        self._providers = providers

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
        domains: tuple[str, ...] = (),
    ) -> SearchResponse:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be blank")

        attempted: list[str] = []
        last_error: SearchProviderError | None = None
        empty_response: SearchResponse | None = None

        for provider in self._providers:
            if not provider.is_available():
                logger.info("Skipping unavailable search provider %s", provider.name)
                continue

            attempted.append(provider.name)
            try:
                response = await provider.search(
                    normalized,
                    max_results=max_results,
                    timeout=timeout,
                    domains=domains,
                )
                if response.results:
                    return response
                if empty_response is None:
                    empty_response = response
                logger.info(
                    "Search provider %s returned no results; trying the next provider",
                    provider.name,
                )
            except SearchProviderError as exc:
                last_error = exc
                logger.warning(
                    "Search provider %s failed (recoverable=%s): %s",
                    provider.name,
                    exc.recoverable,
                    exc,
                )
                continue

        if empty_response is not None:
            return empty_response

        detail = ", ".join(attempted) if attempted else "none"
        message = f"all search providers failed (attempted: {detail})"
        if last_error is not None:
            message = f"{message}; last error: {last_error}"
        raise SearchProviderError(message, provider="router", recoverable=False)


def build_search_router(
    *,
    provider_names: list[str],
    tavily_api_key: str,
    http_client: httpx.AsyncClient,
) -> SearchRouter:
    """Construct providers from a configured name order, ignoring unknowns."""

    providers: list[SearchProvider] = []
    for name in provider_names:
        if name == "tavily":
            providers.append(
                TavilyProvider(api_key=tavily_api_key, http_client=http_client),
            )
        elif name == "duckduckgo":
            providers.append(DuckDuckGoProvider())
        else:
            logger.warning("Ignoring unknown search provider %r", name)

    return SearchRouter(providers)
