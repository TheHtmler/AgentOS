import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import trafilatura

from agent_api.tools.fetch.truncate import apply_fetch_limits
from agent_api.tools.fetch.types import FetchProviderError, FetchResponse
from agent_api.tools.fetch.url_guard import UnsafeUrlError, assert_public_http_url, get_public_url


class LocalFetchProvider:
    """Fetch public HTML pages and extract main text without a third-party key."""

    name = "local"

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    def is_available(self) -> bool:
        return True

    async def fetch(
        self,
        url: str,
        *,
        max_chars: int,
        timeout: float,
    ) -> FetchResponse:
        try:
            safe_url = assert_public_http_url(url)
        except UnsafeUrlError as exc:
            raise FetchProviderError(
                str(exc),
                provider=self.name,
                recoverable=False,
            ) from exc

        response = await get_public_url(self._http_client, safe_url, timeout=timeout)
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower() and not response.text.lstrip().startswith("<"):
            raise FetchProviderError(
                f"unsupported content type: {content_type or 'unknown'}",
                provider=self.name,
                recoverable=True,
            )

        html = response.text
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            url=safe_url,
        )
        text = (extracted or "").strip()
        if not text:
            text = _strip_tags(html).strip()

        if not text:
            raise FetchProviderError(
                "unable to extract page text",
                provider=self.name,
                recoverable=True,
            )

        title = _extract_title(html) or (urlparse(safe_url).hostname or safe_url)
        outline = _extract_outline(html)
        return apply_fetch_limits(
            provider=self.name,
            url=safe_url,
            title=title,
            outline=outline,
            text=text,
            max_chars=max_chars,
        )


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.headings: list[str] = []
        self._in_title = False
        self._heading_level: int | None = None
        self._heading_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
        elif lower in {"h1", "h2", "h3"} and len(self.headings) < 40:
            self._heading_level = int(lower[1])
            self._heading_chunks = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
        elif lower in {"h1", "h2", "h3"} and self._heading_level is not None:
            text = " ".join("".join(self._heading_chunks).split()).strip()
            if text:
                self.headings.append(f"{'#' * self._heading_level} {text}")
            self._heading_level = None
            self._heading_chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            candidate = " ".join(data.split()).strip()
            if candidate:
                self.title = candidate
        if self._heading_level is not None:
            self._heading_chunks.append(data)


def _extract_title(html: str) -> str:
    parser = _HeadingParser()
    parser.feed(html)
    return parser.title or ""


def _extract_outline(html: str) -> str:
    parser = _HeadingParser()
    parser.feed(html)
    return "\n".join(parser.headings)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _TAG_RE.sub(" ", without_scripts)
