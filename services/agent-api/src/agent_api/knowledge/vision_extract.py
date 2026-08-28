"""Vision-model extraction for Ops knowledge PDF/image import.

Local PaddleOCR only reads a page when its PyMuPDF text layer is too short,
so any diagram embedded on a text-rich page is silently dropped. This module
instead sends every page/image to the remote BACKGROUND_VISION_MODEL, which
can both transcribe text in true reading order and describe charts/diagrams
in prose. Mirrors the BACKGROUND_* call pattern in memory/extract.py.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
import pymupdf

from agent_api.config import Settings
from agent_api.knowledge.ocr_client import prepare_ocr_png

logger = logging.getLogger(__name__)

_MAX_PAGES = 50
_RENDER_DPI = 200
_MAX_CONCURRENT_PAGES = 4
_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

_TRANSCRIBE_PROMPT = (
    "Transcribe this document page completely and accurately, preserving the "
    "true reading order (multi-column layouts and tables read in the order a "
    "human would read them, not raw left-to-right scan order). If the page "
    "contains a chart, diagram, or figure, describe its content and key data "
    "relationships in accurate prose instead of skipping it or outputting "
    "garbled characters. Output only the transcribed text/description in the "
    "document's own language — no preamble, no commentary, no markdown fences."
)


class VisionExtractError(Exception):
    """Raised when the background vision endpoint cannot return usable text."""


def _b64_image_url(image: bytes) -> str:
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def _transcribe_image(
    image: bytes,
    *,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> str:
    """Call the background vision model on one image; always raises VisionExtractError."""

    try:
        response = await http_client.post(
            settings.resolved_background_vision_base_url + "/chat/completions",
            json={
                "model": settings.resolved_background_vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _TRANSCRIBE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": _b64_image_url(image)},
                            },
                        ],
                    },
                ],
                "max_tokens": 2048,
                "temperature": 0,
            },
            timeout=settings.background_vision_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise VisionExtractError(
            "视觉模型请求超时，请检查 BACKGROUND_VISION_BASE_URL/BACKGROUND_BASE_URL 是否正常，"
            "或增大 BACKGROUND_VISION_TIMEOUT_SECONDS。"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise VisionExtractError(
            f"视觉模型请求失败（HTTP {exc.response.status_code}），"
            "请检查 BACKGROUND_VISION_MODEL 与鉴权配置。",
        ) from exc
    except httpx.HTTPError as exc:
        raise VisionExtractError(f"视觉模型请求失败：{exc}") from exc

    try:
        raw = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VisionExtractError("视觉模型返回内容格式异常") from exc
    if not isinstance(raw, str):
        raise VisionExtractError("视觉模型返回内容格式异常")

    cleaned = _THINK_RE.sub("", raw).strip()
    if not cleaned:
        raise VisionExtractError("视觉模型没有返回可用文本")
    return cleaned


async def extract_image_text_vision(
    data: bytes,
    *,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> str:
    """Transcribe/describe a single standalone image via the background vision model."""

    image = prepare_ocr_png(data)
    return await _transcribe_image(image, http_client=http_client, settings=settings)


async def extract_pdf_text_vision(
    data: bytes,
    *,
    http_client: httpx.AsyncClient,
    settings: Settings,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, int, int]:
    """Vision-transcribe every PDF page; a failed page falls back to its text layer.

    Returns ``(full_text, text_layer_pages, vision_pages)`` — text_layer_pages
    counts pages that fell back to the raw PyMuPDF text layer after the vision
    call failed, vision_pages counts pages the vision model transcribed.
    ``on_progress(settled, total)`` fires as pages finish, for the Ops import
    progress display.
    """

    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = cast(int, document.page_count)  # pyright: ignore[reportUnknownMemberType]
        if page_count > _MAX_PAGES:
            raise ValueError(f"PDF 超过 {_MAX_PAGES} 页上限")

        fallback_texts: list[str] = []
        images: list[bytes] = []
        for page in document:
            text = cast(
                str,
                page.get_text("text"),  # pyright: ignore[reportUnknownMemberType]
            ).strip()
            fallback_texts.append(text)
            pixmap = page.get_pixmap(dpi=_RENDER_DPI)  # pyright: ignore[reportUnknownMemberType]
            images.append(cast(bytes, pixmap.tobytes("png")))  # pyright: ignore[reportUnknownMemberType]

    page_texts: list[str | None] = [None] * page_count
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)
    vision_pages = 0
    fallback_pages = 0
    settled_pages = 0

    async def transcribe_one(index: int) -> None:
        nonlocal vision_pages, fallback_pages, settled_pages
        async with semaphore:
            try:
                page_texts[index] = await _transcribe_image(
                    images[index],
                    http_client=http_client,
                    settings=settings,
                )
                vision_pages += 1
            except VisionExtractError as error:
                logger.warning(
                    "Vision transcription failed for PDF page %d/%d: %s",
                    index + 1,
                    page_count,
                    error,
                )
                fallback = fallback_texts[index]
                if fallback:
                    page_texts[index] = fallback
                    fallback_pages += 1
            settled_pages += 1
            if on_progress is not None:
                await on_progress(settled_pages, page_count)

    if on_progress is not None:
        await on_progress(0, page_count)
    await asyncio.gather(*(transcribe_one(index) for index in range(page_count)))

    full_text = "\n\n".join(
        f"[第 {index + 1} 页]\n{text}" for index, text in enumerate(page_texts) if text
    ).strip()
    if not full_text:
        raise ValueError("未能从 PDF 提取到正文")

    return full_text, fallback_pages, vision_pages
