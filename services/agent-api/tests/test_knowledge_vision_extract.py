import json

import httpx
import pymupdf
import pytest

from agent_api.config import Settings
from agent_api.knowledge.vision_extract import extract_image_text_vision, extract_pdf_text_vision


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        "background_base_url": "http://vision.test",
        "background_vision_model": "vision-model",
        # Explicit empties keep these tests independent of the developer's .env.
        "background_vision_base_url": "",
        "background_vision_api_key": "",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _pdf_bytes(*page_texts: str) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)  # pyright: ignore[reportUnknownMemberType]
    data = document.tobytes()  # pyright: ignore[reportUnknownMemberType]
    document.close()
    return data


@pytest.mark.anyio
async def test_extract_pdf_text_vision_sends_one_request_per_page() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "转录内容"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await extract_pdf_text_vision(
            _pdf_bytes("", "", ""),
            http_client=client,
            settings=_settings(),
        )

    assert len(requests) == 3
    assert requests[0].url == httpx.URL("http://vision.test/chat/completions")
    assert json.loads(requests[0].content)["model"] == "vision-model"

    full_text, text_layer_pages, vision_pages = result
    assert text_layer_pages == 0
    assert vision_pages == 3
    for index in range(3):
        assert f"[第 {index + 1} 页]\n转录内容" in full_text


@pytest.mark.anyio
async def test_extract_pdf_text_vision_uses_dedicated_vision_base_url() -> None:
    """A vision override reroutes transcription while the shared endpoint stays put."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "转录内容"}}]})

    transport = httpx.MockTransport(handler)
    settings = _settings(
        background_base_url="http://shared.test",
        background_vision_base_url="http://vision-only.test/",
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await extract_pdf_text_vision(
            _pdf_bytes(""),
            http_client=client,
            settings=settings,
        )

    assert len(requests) == 1
    assert requests[0].url == httpx.URL("http://vision-only.test/chat/completions")


@pytest.mark.anyio
async def test_extract_pdf_text_vision_falls_back_to_text_layer_on_failure() -> None:
    text = "This page has a real text layer used as the failure fallback."

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await extract_pdf_text_vision(
            _pdf_bytes(text),
            http_client=client,
            settings=_settings(),
        )

    assert result == (f"[第 1 页]\n{text}", 1, 0)


@pytest.mark.anyio
async def test_extract_pdf_text_vision_raises_when_no_page_has_usable_text() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="未能从 PDF 提取到正文"):
            await extract_pdf_text_vision(
                _pdf_bytes(""),
                http_client=client,
                settings=_settings(),
            )


@pytest.mark.anyio
async def test_extract_pdf_text_vision_over_page_cap_is_rejected() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="PDF 超过 50 页上限"):
            await extract_pdf_text_vision(
                _pdf_bytes(*([""] * 51)),
                http_client=client,
                settings=_settings(),
            )

    assert calls == 0


@pytest.mark.anyio
async def test_extract_image_text_vision_transcribes_via_background_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://vision.test/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "识别出的图片文字"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        text = await extract_image_text_vision(
            b"\xff\xd8\xffimage",
            http_client=client,
            settings=_settings(),
        )

    assert text == "识别出的图片文字"


@pytest.mark.anyio
async def test_extract_image_text_vision_strips_think_tags() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "<think>推理过程...</think>\n识别出的正文"}},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        text = await extract_image_text_vision(
            b"\xff\xd8\xffimage",
            http_client=client,
            settings=_settings(),
        )

    assert text == "识别出的正文"
