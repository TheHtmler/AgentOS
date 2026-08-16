import logging
from typing import cast

import httpx
import pymupdf

from agent_api.config import Settings
from agent_api.knowledge.ocr_client import OcrError, ocr_image_bytes

logger = logging.getLogger(__name__)

# pymupdf's default render is 72dpi, far too coarse for dense CJK documents —
# the OCR service then legitimately "finds no text". 200dpi is the practical
# sweet spot for PaddleOCR on scanned reports.
OCR_RENDER_DPI = 200


async def extract_pdf_text(
    data: bytes,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> tuple[str, int, int]:
    """Extract embedded text from PDF pages."""

    page_texts: list[tuple[int, str]] = []
    text_layer_pages = 0
    ocr_pages = 0

    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = cast(int, document.page_count)  # pyright: ignore[reportUnknownMemberType]
        if page_count > 50:
            raise ValueError("PDF 超过 50 页上限")

        page_number = 0
        for page in document:
            page_number += 1
            text = cast(
                str,
                page.get_text("text"),  # pyright: ignore[reportUnknownMemberType]
            ).strip()
            if len(text) >= settings.ocr_text_min_chars:
                page_texts.append((page_number, text))
                text_layer_pages += 1
            elif settings.ocr_enabled:
                pixmap = page.get_pixmap(dpi=OCR_RENDER_DPI)  # pyright: ignore[reportUnknownMemberType]
                image = cast(
                    bytes,
                    pixmap.tobytes("png"),  # pyright: ignore[reportUnknownMemberType]
                )
                try:
                    text = await ocr_image_bytes(image, client=client, settings=settings)
                except OcrError as error:
                    # One unreadable page (blank, cover, photo-only) must not sink
                    # the whole import; the document-level check below still applies.
                    logger.warning(
                        "OCR failed for PDF page %d/%d: %s",
                        page_number,
                        page_count,
                        error,
                    )
                    continue
                cleaned = text.strip()
                if cleaned:
                    page_texts.append((page_number, cleaned))
                    ocr_pages += 1
            elif text:
                page_texts.append((page_number, text))
                text_layer_pages += 1

    full_text = "\n\n".join(
        f"[第 {number} 页]\n{body}" for number, body in page_texts if body
    ).strip()
    if not full_text:
        raise ValueError("未能从 PDF 提取到正文")

    return full_text, text_layer_pages, ocr_pages
