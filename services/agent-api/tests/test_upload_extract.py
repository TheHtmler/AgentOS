"""Upload storage and text extraction helpers."""

from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest

from agent_api.config import SERVICE_ROOT, Settings
from agent_api.uploads.extract import extract_upload_text
from agent_api.uploads.storage import store_upload


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


def test_upload_settings_defaults() -> None:
    settings = _settings()
    assert settings.upload_root == SERVICE_ROOT / "data" / "uploads"
    assert settings.upload_max_bytes == 20_000_000
    assert settings.upload_max_files_per_message == 3


def test_store_upload_writes_file_with_safe_filename(tmp_path: Path) -> None:
    owner_user_id = uuid4()
    artifact_id = uuid4()
    data = b"lab report bytes"

    stored_path = store_upload(
        root=tmp_path,
        owner_user_id=owner_user_id,
        artifact_id=artifact_id,
        filename="../../../etc/passwd",
        data=data,
    )

    assert stored_path.is_absolute()
    assert stored_path.read_bytes() == data
    assert stored_path.parent == tmp_path / str(owner_user_id) / str(artifact_id)
    assert stored_path.name == "passwd"


@pytest.mark.anyio
async def test_extract_upload_text_image_uses_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ocr(image: bytes, **_kwargs: object) -> str:
        assert image == b"\xff\xd8\xffimage"
        return "化验单 OCR 正文"

    monkeypatch.setattr("agent_api.uploads.extract.ocr_image_bytes", fake_ocr)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        text, meta = await extract_upload_text(
            data=b"\xff\xd8\xffimage",
            filename="report.jpg",
            mime_type="image/jpeg",
            client=client,
            settings=settings,
        )

    assert text == "化验单 OCR 正文"
    assert meta == {"ocr_pages": 1, "text_layer_pages": 0}


@pytest.mark.anyio
async def test_extract_upload_text_pdf_uses_pdf_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_data = _pdf_bytes(
        "This PDF page has enough embedded text to use the text layer directly.",
    )

    async def fake_pdf_extract(
        data: bytes,
        *,
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> tuple[str, int, int]:
        assert data == pdf_data
        assert client is not None
        assert settings is not None
        return ("PDF 正文", 1, 0)

    monkeypatch.setattr("agent_api.uploads.extract.extract_pdf_text", fake_pdf_extract)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        text, meta = await extract_upload_text(
            data=pdf_data,
            filename="report.pdf",
            mime_type="application/pdf",
            client=client,
            settings=settings,
        )

    assert text == "PDF 正文"
    assert meta == {"ocr_pages": 0, "text_layer_pages": 1}


@pytest.mark.anyio
async def test_extract_upload_text_unsupported_mime_raises() -> None:
    settings = _settings()
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="不支持的上传类型"):
            await extract_upload_text(
                data=b"plain text",
                filename="notes.txt",
                mime_type="text/plain",
                client=client,
                settings=settings,
            )
