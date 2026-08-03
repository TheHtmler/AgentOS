from typing import Any, cast
from urllib.parse import urlparse

import httpx

from agent_api.tools.fetch.truncate import apply_fetch_limits
from agent_api.tools.fetch.types import FetchProviderError, FetchResponse
from agent_api.tools.fetch.url_guard import UnsafeUrlError, assert_public_http_url

_DEFAULT_BASE_URL = "https://api.firecrawl.dev"


class FirecrawlProvider:
    """HTTP adapter for Firecrawl's scrape API."""

    name = "firecrawl"

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.AsyncClient,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        client_base = str(http_client.base_url) if http_client.base_url else ""
        self._base_url = client_base.rstrip("/") if client_base else base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self._api_key.strip())

    async def fetch(
        self,
        url: str,
        *,
        max_chars: int,
        timeout: float,
    ) -> FetchResponse:
        if not self.is_available():
            raise FetchProviderError(
                "Firecrawl API key is not configured",
                provider=self.name,
                recoverable=True,
            )

        try:
            safe_url = assert_public_http_url(url)
        except UnsafeUrlError as exc:
            raise FetchProviderError(
                str(exc),
                provider=self.name,
                recoverable=False,
            ) from exc

        endpoint = f"{self._base_url}/v1/scrape"
        payload = {
            "url": safe_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }

        try:
            response = await self._http_client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise FetchProviderError(
                "Firecrawl request timed out",
                provider=self.name,
                recoverable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchProviderError(
                f"Firecrawl transport error: {exc}",
                provider=self.name,
                recoverable=True,
            ) from exc

        if response.status_code in {401, 402, 403, 429} or response.status_code >= 500:
            raise FetchProviderError(
                f"Firecrawl HTTP {response.status_code}",
                provider=self.name,
                recoverable=True,
            )

        if response.status_code >= 400:
            raise FetchProviderError(
                f"Firecrawl HTTP {response.status_code}",
                provider=self.name,
                recoverable=True,
            )

        try:
            body = cast(dict[str, Any], response.json())
        except ValueError as exc:
            raise FetchProviderError(
                "Firecrawl returned invalid JSON",
                provider=self.name,
                recoverable=True,
            ) from exc

        if body.get("success") is False:
            raise FetchProviderError(
                "Firecrawl scrape unsuccessful",
                provider=self.name,
                recoverable=True,
            )

        data_obj = body.get("data")
        data = cast(dict[str, Any], data_obj) if isinstance(data_obj, dict) else {}
        markdown_obj = data.get("markdown")
        text = markdown_obj if isinstance(markdown_obj, str) else ""
        metadata_obj = data.get("metadata")
        metadata = (
            cast(dict[str, Any], metadata_obj) if isinstance(metadata_obj, dict) else {}
        )
        title_obj = metadata.get("title")
        title = title_obj.strip() if isinstance(title_obj, str) else ""
        if not title:
            title = urlparse(safe_url).hostname or safe_url

        if not text.strip():
            raise FetchProviderError(
                "Firecrawl returned empty content",
                provider=self.name,
                recoverable=True,
            )

        outline = _outline_from_markdown(text)
        return apply_fetch_limits(
            provider=self.name,
            url=safe_url,
            title=title,
            outline=outline,
            text=text,
            max_chars=max_chars,
        )


def _outline_from_markdown(markdown: str) -> str:
    headings: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append(stripped)
        if len(headings) >= 40:
            break
    return "\n".join(headings)
