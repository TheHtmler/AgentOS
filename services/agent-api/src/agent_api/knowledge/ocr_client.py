from typing import Any, cast

import httpx

from agent_api.config import Settings


class OcrError(Exception):
    """Raised when the PaddleOCR HTTP adapter cannot return text."""


def _resolve_base_url(client: httpx.AsyncClient, settings: Settings) -> str:
    client_base = str(client.base_url) if client.base_url else ""
    if client_base:
        return client_base.rstrip("/")
    return settings.ocr_base_url.rstrip("/")


def _build_headers(settings: Settings) -> dict[str, str]:
    return {"X-API-Key": settings.ocr_api_key}


def _parse_ocr_payload(body: dict[str, Any]) -> str:
    text_value = body.get("text")
    if isinstance(text_value, str):
        return text_value.strip()

    lines_obj = body.get("lines")
    if isinstance(lines_obj, list):
        parts: list[str] = []
        for raw_line in cast(list[object], lines_obj):
            if not isinstance(raw_line, dict):
                continue
            line = cast(dict[str, object], raw_line)
            line_text = str(line.get("text") or "").strip()
            if line_text:
                parts.append(line_text)
        return "\n".join(parts)

    return ""


async def _post_ocr(
    *,
    client: httpx.AsyncClient,
    url: str,
    image: bytes,
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    return await client.post(
        url,
        files={"file": ("image.bin", image, "application/octet-stream")},
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

    base_url = _resolve_base_url(client, settings)
    headers = _build_headers(settings)
    timeout = settings.ocr_timeout_seconds
    file_url = f"{base_url}/ocr/file"
    legacy_url = f"{base_url}/ocr"

    try:
        response = await _post_ocr(
            client=client,
            url=file_url,
            image=image,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code == 404:
            response = await _post_ocr(
                client=client,
                url=legacy_url,
                image=image,
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

    try:
        body = cast(dict[str, Any], response.json())
    except ValueError as exc:
        raise OcrError("OCR 服务返回无效 JSON。") from exc

    text = _parse_ocr_payload(body)
    if not text:
        raise OcrError("OCR 服务未识别到文字内容。")

    return text
