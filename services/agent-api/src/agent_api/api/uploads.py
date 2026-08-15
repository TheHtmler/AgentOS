"""Authenticated report upload API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.artifact_store import ArtifactScopeError, create_artifact
from agent_api.db.models import Thread, User
from agent_api.db.session import session_factory
from agent_api.knowledge.ocr_client import OcrError
from agent_api.uploads import extract_upload_text, store_upload

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

_ALLOWED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})
_ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)


class UploadResponse(BaseModel):
    artifact_id: UUID
    title: str
    mime_type: str
    content_chars: int
    ocr_pages: int
    text_layer_pages: int
    case_id: UUID | None


def _validate_file(filename: str, mime_type: str) -> None:
    if Path(filename).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise ValueError("file extension must be pdf, png, jpg, jpeg, or webp")
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise ValueError("file MIME type must be PDF, PNG, JPEG, or WebP")


@router.post("", response_model=UploadResponse)
async def post_upload(
    user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
    thread_id: Annotated[UUID, Form()],
    title: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Extract and persist one report owned by the current Thread user."""

    settings = get_settings()
    filename = file.filename or "upload"
    mime_type = (file.content_type or "").split(";", 1)[0].strip().lower()

    try:
        _validate_file(filename, mime_type)
        data = await file.read(settings.upload_max_bytes + 1)
        if len(data) > settings.upload_max_bytes:
            raise ValueError(f"file exceeds {settings.upload_max_bytes} bytes")

        async with session_factory() as session:
            thread = await session.scalar(
                select(Thread).where(
                    Thread.id == thread_id,
                    Thread.user_id == user.id,
                    Thread.deleted_at.is_(None),
                )
            )
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        # OCR is best-effort backup text. Originals always land in UPLOAD_ROOT so
        # vision analysis can proceed even when PaddleOCR is down.
        extracted_text = ""
        extraction_meta: dict[str, object] = {
            "ocr_pages": 0,
            "text_layer_pages": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.ocr_timeout_seconds) as client:
                extracted_text, extraction_meta = await extract_upload_text(
                    data=data,
                    filename=filename,
                    mime_type=mime_type,
                    client=client,
                    settings=settings,
                )
        except OcrError as error:
            extraction_meta = {
                "ocr_pages": 0,
                "text_layer_pages": 0,
                "ocr_status": "failed",
                "ocr_error": str(error),
            }

        content = extracted_text[: settings.artifact_max_chars]
        artifact_title = (title or "").strip() or Path(filename).stem or filename
        base_meta: dict[str, object] = {
            "original_filename": filename,
            "byte_size": len(data),
            **extraction_meta,
        }
        if extracted_text and len(content) < len(extracted_text):
            base_meta["content_truncated"] = True

        async with session_factory() as session, session.begin():
            artifact = await create_artifact(
                session,
                owner_user_id=user.id,
                kind="upload",
                title=artifact_title,
                content=content,
                mime_type=mime_type,
                case_id=thread.case_id,
                thread_id=thread.id,
                meta=base_meta,
            )
            stored_path = store_upload(
                root=settings.upload_root,
                owner_user_id=user.id,
                artifact_id=artifact.id,
                filename=filename,
                data=data,
            )
            artifact.meta = {
                **base_meta,
                "stored_path": str(stored_path.relative_to(settings.upload_root.resolve())),
            }

            return UploadResponse(
                artifact_id=artifact.id,
                title=artifact.title,
                mime_type=artifact.mime_type,
                content_chars=artifact.content_chars,
                ocr_pages=int(extraction_meta.get("ocr_pages", 0)),
                text_layer_pages=int(extraction_meta.get("text_layer_pages", 0)),
                case_id=artifact.case_id,
            )
    except HTTPException:
        raise
    except ArtifactScopeError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
