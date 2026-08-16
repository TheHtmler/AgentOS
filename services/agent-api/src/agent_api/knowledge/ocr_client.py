from __future__ import annotations

import logging
from typing import Any, cast

import httpx
import pymupdf

from agent_api.config import Settings

logger = logging.getLogger(__name__)

_MAX_OCR_EDGE = 2000
_MIN_OCR_EDGE = 720


class OcrError(Exception):
    """Raised when the PaddleOCR HTTP adapter cannot return text."""


def _resolve_base_url(client: httpx.AsyncClient, settings: Settings) -> str:
    client_base = str(client.base_url) if client.base_url else ""
    if client_base:
        return client_base.rstrip("/")
    return settings.ocr_base_url.rstrip("/")


def _build_headers(settings: Settings) -> dict[str, str]:
    return {"X-API-Key": settings.ocr_api_key}


def _parse_ocr_payload(body: object) -> str:
    """Normalize common PaddleOCR / PaddleX HTTP shapes to plain text."""

    if isinstance(body, str):
        return body.strip()
    if isinstance(body, list):
        return _parse_ocr_list(cast(list[object], body))
    if not isinstance(body, dict):
        return ""

    payload = cast(dict[str, Any], body)
    text_value = payload.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return text_value.strip()

    rec_texts = payload.get("rec_texts") or payload.get("rec_text")
    if isinstance(rec_texts, list):
        parts = [str(item).strip() for item in cast(list[object], rec_texts) if str(item).strip()]
        if parts:
            return "\n".join(parts)

    skip = {"text", "msg", "message", "code", "status", "error", "detail", "success"}
    for key, nested in payload.items():
        if key in skip or not isinstance(nested, dict | list):
            continue
        parsed = _parse_ocr_payload(cast(object, nested))
        if parsed:
            return parsed
    return ""


def _parse_ocr_list(items: list[object]) -> str:
    if len(items) == 2 and _looks_like_paddle_line(items):
        return _paddle_line_text(items)

    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                parts.append(item.strip())
            continue
        if isinstance(item, list):
            row = cast(list[object], item)
            if _looks_like_paddle_line(row):
                text = _paddle_line_text(row)
                if text:
                    parts.append(text)
                continue
        parsed = _parse_ocr_payload(cast(object, item))
        if parsed:
            parts.append(parsed)
    return "\n".join(parts)


def _looks_like_paddle_line(item: object) -> bool:
    if not isinstance(item, list):
        return False
    row = cast(list[object], item)
    if len(row) != 2:
        return False
    second = row[1]
    if isinstance(second, str):
        return True
    if isinstance(second, list):
        values = cast(list[object], second)
        return bool(values) and isinstance(values[0], str)
    if isinstance(second, tuple):
        values = cast(tuple[object, ...], second)
        return bool(values) and isinstance(values[0], str)
    return False


def _paddle_line_text(item: list[object]) -> str:
    second = item[1]
    if isinstance(second, str):
        return second.strip()
    if isinstance(second, list):
        values = cast(list[object], second)
        return str(values[0]).strip() if values else ""
    if isinstance(second, tuple):
        values = cast(tuple[object, ...], second)
        return str(values[0]).strip() if values else ""
    return ""


def prepare_ocr_png(image: bytes) -> bytes:
    """Re-encode photos as PNG at an OCR-friendly size.

    Phone JPEGs are often too large, EXIF-rotated, or sent as anonymous
    ``image.bin``. PDF pages already go through PyMuPDF as PNG; photos should too.
    """

    try:
        document = pymupdf.open(stream=image)
    except Exception:
        return image
    try:
        page_count = cast(int, document.page_count)  # pyright: ignore[reportUnknownMemberType]
        if page_count < 1:
            return image
        page = document[0]
        width = float(page.rect.width)
        height = float(page.rect.height)
        longest = max(width, height)
        if longest <= 0:
            return image
        zoom = 1.0
        if longest > _MAX_OCR_EDGE:
            zoom = _MAX_OCR_EDGE / longest
        elif longest < _MIN_OCR_EDGE:
            zoom = min(_MIN_OCR_EDGE / longest, 2.0)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))  # pyright: ignore[reportUnknownMemberType]
        return cast(bytes, pixmap.tobytes("png"))  # pyright: ignore[reportUnknownMemberType]
    except Exception:
        logger.warning("Unable to normalize image for OCR; sending original bytes")
        return image
    finally:
        document.close()


async def _post_ocr(
    *,
    client: httpx.AsyncClient,
    url: str,
    image: bytes,
    filename: str,
    content_type: str,
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    return await client.post(
        url,
        files={"file": (filename, image, content_type)},
        headers=headers,
        timeout=timeout,
    )


async def ocr_image_bytes(
    image: bytes,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> str:
    """Call Mac mini PaddleOCR over HTTP and return normalized plain text."""

    payload = prepare_ocr_png(image)
    filename = "ocr.png"
    content_type = "image/png"
    base_url = _resolve_base_url(client, settings)
    headers = _build_headers(settings)
    timeout = settings.ocr_timeout_seconds
    file_url = f"{base_url}/ocr/file"
    legacy_url = f"{base_url}/ocr"

    try:
        response = await _post_ocr(
            client=client,
            url=file_url,
            image=payload,
            filename=filename,
            content_type=content_type,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code == 404 or (
            response.status_code < 400 and not _response_text(response)
        ):
            response = await _post_ocr(
                client=client,
                url=legacy_url,
                image=payload,
                filename=filename,
                content_type=content_type,
                headers=headers,
                timeout=timeout,
            )
    except httpx.TimeoutException as exc:
        raise OcrError("OCR 服务请求超时，请检查 PaddleOCR 是否正常运行。") from exc
    except httpx.HTTPError as exc:
        raise OcrError(f"OCR 服务连接失败：{exc}") from exc

    if response.status_code >= 400:
        raise OcrError(
            f"OCR 服务不可用（HTTP {response.status_code}），请检查 OCR_BASE_URL 与 API Key。"
        )

    text = _response_text(response)
    if not text:
        logger.warning("OCR returned no readable text; status=%s", response.status_code)
        raise OcrError("OCR 没有读出文字。请换更清晰的 jpg/png，或先转成 PDF 再导入。")

    return text


def _response_text(response: httpx.Response) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return ""
    return _parse_ocr_payload(body)
