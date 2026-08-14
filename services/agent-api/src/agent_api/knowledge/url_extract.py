from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import trafilatura


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            candidate = " ".join(data.split()).strip()
            if candidate:
                self.title = candidate


_TAG_RE = re.compile(r"<[^>]+>")


def _extract_title(html: str, url: str) -> str:
    parser = _TitleParser()
    parser.feed(html)
    if parser.title:
        return parser.title
    return urlparse(url).hostname or url


def _strip_tags(html: str) -> str:
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _TAG_RE.sub(" ", without_scripts)


async def fetch_url_text(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_bytes: int = 5_000_000,
) -> tuple[str, str]:
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    content = response.content
    if len(content) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")

    html = content.decode(response.encoding or "utf-8", errors="replace")
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
        url=url,
    )
    text = (extracted or "").strip()
    if not text:
        text = _strip_tags(html).strip()
    if not text:
        raise ValueError("unable to extract page text")

    return _extract_title(html, url), text
