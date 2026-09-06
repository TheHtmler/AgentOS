"""Owner-scoped user controls for persisted memories and Artifacts."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.models import Artifact, Thread, User, UserMemory
from agent_api.db.session import session_factory
from agent_api.uploads.storage import resolve_stored_upload_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/me", tags=["user-data"])


class MemoryResponse(BaseModel):
    id: UUID
    agent_id: UUID
    case_id: UUID | None
    kind: str
    key: str | None
    content: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]


class ArtifactResponse(BaseModel):
    id: UUID
    case_id: UUID | None
    thread_id: UUID | None
    kind: str
    title: str
    mime_type: str
    content_chars: int
    created_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]


class FileLibraryItemResponse(BaseModel):
    """A user-visible uploaded file with its durable original bytes."""

    id: UUID
    title: str
    original_filename: str
    mime_type: str
    byte_size: int | None
    thread_id: UUID | None
    thread_agent_id: UUID | None
    created_at: datetime


class FileLibraryListResponse(BaseModel):
    files: list[FileLibraryItemResponse]


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(user: Annotated[User, Depends(get_current_user)]) -> MemoryListResponse:
    async with session_factory() as session:
        rows = await session.scalars(
            select(UserMemory)
            .where(UserMemory.user_id == user.id, UserMemory.status == "active")
            .order_by(UserMemory.updated_at.desc())
        )
        memories = list(rows)
    return MemoryListResponse(
        memories=[
            MemoryResponse(
                id=row.id,
                agent_id=row.agent_id,
                case_id=row.case_id,
                kind=row.kind,
                key=row.key,
                content=row.content,
                tags=list(row.tags),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in memories
        ]
    )


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: UUID, user: Annotated[User, Depends(get_current_user)]) -> None:
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user.id)
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        await session.delete(row)


@router.get("/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(user: Annotated[User, Depends(get_current_user)]) -> ArtifactListResponse:
    async with session_factory() as session:
        rows = await session.scalars(
            select(Artifact)
            .where(Artifact.owner_user_id == user.id)
            .order_by(Artifact.created_at.desc())
            .limit(200)
        )
        artifacts = list(rows)
    return ArtifactListResponse(
        artifacts=[
            ArtifactResponse(
                id=row.id,
                case_id=row.case_id,
                thread_id=row.thread_id,
                kind=row.kind,
                title=row.title,
                mime_type=row.mime_type,
                content_chars=row.content_chars,
                created_at=row.created_at,
            )
            for row in artifacts
        ]
    )


@router.get("/files", response_model=FileLibraryListResponse)
async def list_files(user: Annotated[User, Depends(get_current_user)]) -> FileLibraryListResponse:
    """List original uploads that are safe to expose in the user's file library.

    Artifacts also carry internal fetch bodies and sandbox stdout. Those are model
    working data, not user files, so this intentionally includes only uploads
    with the existing owner check.
    """

    async with session_factory() as session:
        rows = await session.execute(
            select(Artifact, Thread.agent_id)
            .outerjoin(Thread, Artifact.thread_id == Thread.id)
            .where(Artifact.owner_user_id == user.id, Artifact.kind == "upload")
            .order_by(Artifact.created_at.desc())
            .limit(200)
        )

        files = [
            FileLibraryItemResponse(
                id=artifact.id,
                title=artifact.title,
                original_filename=(
                    str(artifact.meta.get("original_filename"))
                    if isinstance(artifact.meta, dict)
                    and isinstance(artifact.meta.get("original_filename"), str)
                    else artifact.title
                ),
                mime_type=artifact.mime_type,
                byte_size=(
                    value
                    if isinstance(artifact.meta, dict)
                    and isinstance((value := artifact.meta.get("byte_size")), int)
                    else None
                ),
                thread_id=artifact.thread_id,
                thread_agent_id=agent_id,
                created_at=artifact.created_at,
            )
            for artifact, agent_id in rows.tuples()
        ]

    return FileLibraryListResponse(files=files)


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    settings = get_settings()
    stored_path: Path | None = None
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_user_id == user.id)
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if row.kind == "upload":
            meta = row.meta if isinstance(row.meta, dict) else None
            stored_path = resolve_stored_upload_path(root=settings.upload_root, meta=meta)
        await session.delete(row)

    if stored_path is not None:
        try:
            shutil.rmtree(stored_path.parent)
        except OSError:
            # The row is already hidden from all reads. A later orphan sweep can
            # reclaim an inaccessible file without resurrecting the Artifact.
            logger.exception("failed to remove upload bytes for deleted artifact %s", artifact_id)
