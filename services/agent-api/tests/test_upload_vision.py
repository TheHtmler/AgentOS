"""Unit tests for upload vision attachment helpers."""

from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from pydantic_ai.messages import BinaryContent

from agent_api.config import get_settings
from agent_api.db.models import Artifact
from agent_api.uploads.prompt import enrich_ag_ui_user_message, user_prompt_with_vision
from agent_api.uploads.vision import _pdf_page_pngs, _resolve_stored_file
from ag_ui.core import UserMessage


def test_user_prompt_with_vision_appends_binary_parts() -> None:
    part = BinaryContent(data=b"abc", media_type="image/png")
    prompt = user_prompt_with_vision("请解读", [part])
    assert isinstance(prompt, list)
    assert prompt[0] == "请解读"
    assert prompt[1] is part


def test_enrich_ag_ui_user_message_adds_binary_input() -> None:
    message = UserMessage(id="u1", role="user", content="请解读 artifact_id=...")
    part = BinaryContent(data=b"\x89PNG", media_type="image/png")
    enriched = enrich_ag_ui_user_message(message, text="请解读", vision_parts=[part])
    assert isinstance(enriched.content, list)
    assert enriched.content[0].type == "text"
    assert enriched.content[1].type == "image"
    assert enriched.content[1].source.mime_type == "image/png"
    assert enriched.content[1].source.value


def test_resolve_stored_file_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    artifact = Artifact(
        id=uuid4(),
        owner_user_id=uuid4(),
        kind="upload",
        title="x",
        mime_type="image/png",
        content="c",
        content_chars=1,
        meta={"stored_path": "../outside.png"},
    )
    assert _resolve_stored_file(artifact, upload_root=root) is None


def test_pdf_page_pngs_renders_first_pages() -> None:
    document = pymupdf.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "lab report")
        data = document.tobytes()
    finally:
        document.close()

    pages = _pdf_page_pngs(data, max_pages=1)
    assert len(pages) == 1
    assert pages[0][:4] == b"\x89PNG"


def test_settings_include_vision_flags() -> None:
    settings = get_settings()
    assert settings.upload_vision_enabled is True
    assert settings.upload_vision_max_images >= 1
    assert settings.upload_vision_max_pdf_pages >= 1
