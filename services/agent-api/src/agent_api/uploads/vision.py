"""Load original upload bytes as multimodal BinaryContent for vision models."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast
from uuid import UUID

import pymupdf
from pydantic_ai.messages import BinaryContent
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import Settings, get_settings
from agent_api.db.artifact_store import get_owned_artifact
from agent_api.db.models import Artifact
from agent_api.uploads.context import parse_artifact_ids
from agent_api.uploads.storage import resolve_stored_upload_path

logger = logging.getLogger(__name__)

_IMAGE_MIME = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})


def _resolve_stored_file(artifact: Artifact, *, upload_root: Path) -> Path | None:
    meta = artifact.meta if isinstance(artifact.meta, dict) else None
    path = resolve_stored_upload_path(root=upload_root, meta=meta)
    if path is None and isinstance(meta, dict) and meta.get("stored_path"):
        logger.warning("upload path escapes UPLOAD_ROOT for artifact %s", artifact.id)
    return path


def _pdf_page_pngs(data: bytes, *, max_pages: int) -> list[bytes]:
    images: list[bytes] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = cast(int, document.page_count)  # pyright: ignore[reportUnknownMemberType]
        limit = min(page_count, max(0, max_pages))
        for index in range(limit):
            page = document.load_page(index)  # pyright: ignore[reportUnknownMemberType]
            pixmap = page.get_pixmap(dpi=144)  # pyright: ignore[reportUnknownMemberType]
            images.append(cast(bytes, pixmap.tobytes("png")))  # pyright: ignore[reportUnknownMemberType]
    return images


async def load_upload_vision_parts(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    case_id: UUID | None,
    user_text: str,
    settings: Settings | None = None,
) -> list[BinaryContent]:
    """Return BinaryContent parts for referenced uploads (images / PDF page renders)."""

    cfg = settings or get_settings()
    if not cfg.upload_vision_enabled:
        return []

    parts: list[BinaryContent] = []
    seen: set[UUID] = set()
    for artifact_id in parse_artifact_ids(user_text):
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        if len(parts) >= cfg.upload_vision_max_images:
            break

        artifact = await get_owned_artifact(
            session,
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            case_id=case_id,
        )
        if artifact is None or artifact.kind != "upload":
            continue

        path = _resolve_stored_file(artifact, upload_root=cfg.upload_root)
        if path is None:
            continue

        mime = (artifact.mime_type or "").split(";", 1)[0].strip().lower()
        try:
            data = path.read_bytes()
        except OSError:
            logger.exception("failed reading upload file for artifact %s", artifact.id)
            continue

        if mime in _IMAGE_MIME:
            media = "image/jpeg" if mime == "image/jpg" else mime
            parts.append(BinaryContent(data=data, media_type=media))
            continue

        if mime == "application/pdf":
            try:
                page_pngs = _pdf_page_pngs(data, max_pages=cfg.upload_vision_max_pdf_pages)
            except Exception:
                logger.exception("failed rendering PDF pages for artifact %s", artifact.id)
                continue
            for png in page_pngs:
                if len(parts) >= cfg.upload_vision_max_images:
                    break
                parts.append(BinaryContent(data=png, media_type="image/png"))

    return parts
