import httpx
import pytest

from agent_api.config import Settings
from agent_api.knowledge.ocr_client import OcrError, ocr_image_bytes


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


@pytest.mark.anyio
async def test_ocr_client_parses_lines_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ocr/file"
        assert request.headers.get("x-api-key") == "secret-key"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"lines": [{"text": "你好", "confidence": 0.9, "box": []}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8787",
    ) as client:
        text = await ocr_image_bytes(
            b"\x89PNG\r\n",
            client=client,
            settings=_settings(ocr_api_key="secret-key"),
        )

    assert "你好" in text


@pytest.mark.anyio
async def test_ocr_client_parses_text_payload() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/ocr/file":
            return httpx.Response(404, json={"detail": "not found"})
        if request.url.path == "/ocr":
            return httpx.Response(200, json={"text": "一行"})
        raise AssertionError(f"unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8787",
    ) as client:
        text = await ocr_image_bytes(
            b"\xff\xd8\xff",
            client=client,
            settings=_settings(),
        )

    assert text == "一行"
    assert calls == ["/ocr/file", "/ocr"]


@pytest.mark.anyio
async def test_ocr_client_raises_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "busy"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8787",
    ) as client:
        with pytest.raises(OcrError, match="OCR 服务"):
            await ocr_image_bytes(b"image", client=client, settings=_settings())
