import logging

import httpx

from agent_api.tools.fetch.base import FetchProvider
from agent_api.tools.fetch.firecrawl import FirecrawlProvider
from agent_api.tools.fetch.local import LocalFetchProvider
from agent_api.tools.fetch.types import FetchProviderError, FetchResponse

logger = logging.getLogger(__name__)


class FetchRouter:
    """Try configured fetch providers in order until one succeeds."""

    def __init__(self, providers: list[FetchProvider]) -> None:
        self._providers = providers

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    async def fetch(
        self,
        url: str,
        *,
        max_chars: int,
        timeout: float,
    ) -> FetchResponse:
        normalized = url.strip()
        if not normalized:
            raise ValueError("url must not be blank")

        attempted: list[str] = []
        last_error: FetchProviderError | None = None

        for provider in self._providers:
            if not provider.is_available():
                logger.info("Skipping unavailable fetch provider %s", provider.name)
                continue

            attempted.append(provider.name)
            try:
                return await provider.fetch(
                    normalized,
                    max_chars=max_chars,
                    timeout=timeout,
                )
            except FetchProviderError as exc:
                last_error = exc
                logger.warning(
                    "Fetch provider %s failed (recoverable=%s): %s",
                    provider.name,
                    exc.recoverable,
                    exc,
                )
                if not exc.recoverable:
                    raise
                continue

        detail = ", ".join(attempted) if attempted else "none"
        message = f"all fetch providers failed (attempted: {detail})"
        if last_error is not None:
            message = f"{message}; last error: {last_error}"
        raise FetchProviderError(message, provider="router", recoverable=False)


def build_fetch_router(
    *,
    provider_names: list[str],
    firecrawl_api_key: str,
    http_client: httpx.AsyncClient,
) -> FetchRouter:
    """Construct providers from a configured name order, ignoring unknowns."""

    providers: list[FetchProvider] = []
    for name in provider_names:
        if name == "firecrawl":
            providers.append(
                FirecrawlProvider(api_key=firecrawl_api_key, http_client=http_client),
            )
        elif name == "local":
            providers.append(LocalFetchProvider(http_client=http_client))
        else:
            logger.warning("Ignoring unknown fetch provider %r", name)

    return FetchRouter(providers)
