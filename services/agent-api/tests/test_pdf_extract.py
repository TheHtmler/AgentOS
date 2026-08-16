import httpx
import pymupdf
import pytest

from agent_api.config import Settings
from agent_api.knowledge.pdf_extract import extract_pdf_text


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
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
async def test_text_layer_pdf_does_not_use_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "This PDF page has enough embedded text to use the text layer directly."

    async def unexpected_ocr(*_args: object, **_kwargs: object) -> str:
        pytest.fail("text-layer page must not invoke OCR")

    monkeypatch.setattr("agent_api.knowledge.pdf_extract.ocr_image_bytes", unexpected_ocr)

    async with httpx.AsyncClient() as client:
        result = await extract_pdf_text(
            _pdf_bytes(text),
            client=client,
            settings=_settings(ocr_text_min_chars=40),
        )

    assert result == (text, 1, 0)


@pytest.mark.anyio
async def test_sparse_page_uses_ocr_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ocr(image: bytes, **_kwargs: object) -> str:
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        return "OCR 识别出的正文"

    monkeypatch.setattr("agent_api.knowledge.pdf_extract.ocr_image_bytes", fake_ocr)

    async with httpx.AsyncClient() as client:
        result = await extract_pdf_text(
            _pdf_bytes("short"),
            client=client,
            settings=_settings(ocr_enabled=True, ocr_text_min_chars=40),
        )

    assert result == ("OCR 识别出的正文", 0, 1)


@pytest.mark.anyio
async def test_pdf_over_50_pages_is_rejected() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="PDF 超过 50 页上限"):
            await extract_pdf_text(
                _pdf_bytes(*([""] * 51)),
                client=client,
                settings=_settings(ocr_enabled=False),
            )


@pytest.mark.anyio
async def test_pdf_without_extractable_text_is_rejected() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="未能从 PDF 提取到正文"):
            await extract_pdf_text(
                _pdf_bytes(""),
                client=client,
                settings=_settings(ocr_enabled=False),
            )


@pytest.mark.anyio
async def test_sparse_text_layer_is_kept_when_ocr_is_disabled() -> None:
    async with httpx.AsyncClient() as client:
        result = await extract_pdf_text(
            _pdf_bytes("short"),
            client=client,
            settings=_settings(ocr_enabled=False, ocr_text_min_chars=40),
        )

    assert result == ("short", 1, 0)


@pytest.mark.anyio
async def test_ocr_failure_on_one_page_does_not_fail_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_api.knowledge.ocr_client import OcrError

    calls = 0

    async def flaky_ocr(image: bytes, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OcrError("OCR 服务未识别到文字内容。")
        return "第二页识别出的正文"

    monkeypatch.setattr("agent_api.knowledge.pdf_extract.ocr_image_bytes", flaky_ocr)

    async with httpx.AsyncClient() as client:
        result = await extract_pdf_text(
            _pdf_bytes("", ""),
            client=client,
            settings=_settings(ocr_enabled=True, ocr_text_min_chars=40),
        )

    assert result == ("第二页识别出的正文", 0, 1)


@pytest.mark.anyio
async def test_ocr_render_uses_high_dpi(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_width = 0

    async def probe_ocr(image: bytes, **_kwargs: object) -> str:
        nonlocal seen_width
        # PNG IHDR bytes 16..20 hold the pixel width (big-endian uint32).
        seen_width = int.from_bytes(image[16:20], "big")
        return "正文"

    monkeypatch.setattr("agent_api.knowledge.pdf_extract.ocr_image_bytes", probe_ocr)

    async with httpx.AsyncClient() as client:
        await extract_pdf_text(
            _pdf_bytes(""),
            client=client,
            settings=_settings(ocr_enabled=True, ocr_text_min_chars=40),
        )

    # A4 at 72dpi is ~595px wide; a 200dpi render must be well above 1000px.
    assert seen_width > 1000
