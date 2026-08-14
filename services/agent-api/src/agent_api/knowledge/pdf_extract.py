from typing import cast

import httpx
import pymupdf

from agent_api.config import Settings
from agent_api.knowledge.ocr_client import ocr_image_bytes


async def extract_pdf_text(
    data: bytes,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> tuple[str, int, int]:
    """Extract embedded text from PDF pages."""

    page_texts: list[str] = []
    text_layer_pages = 0
    ocr_pages = 0

    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = cast(int, document.page_count)  # pyright: ignore[reportUnknownMemberType]
        if page_count > 50:
            raise ValueError("PDF 超过 50 页上限")

        for page in document:
            text = cast(
                str,
                page.get_text("text"),  # pyright: ignore[reportUnknownMemberType]
            ).strip()
            if len(text) >= settings.ocr_text_min_chars:
                page_texts.append(text)
                text_layer_pages += 1
            elif settings.ocr_enabled:
                pixmap = page.get_pixmap()  # pyright: ignore[reportUnknownMemberType]
                image = cast(
                    bytes,
                    pixmap.tobytes("png"),  # pyright: ignore[reportUnknownMemberType]
                )
                text = await ocr_image_bytes(image, client=client, settings=settings)
                page_texts.append(text.strip())
                ocr_pages += 1
            elif text:
                page_texts.append(text)
                text_layer_pages += 1

    full_text = "\n\n".join(text for text in page_texts if text).strip()
    if not full_text:
        raise ValueError("未能从 PDF 提取到正文")

    return full_text, text_layer_pages, ocr_pages
