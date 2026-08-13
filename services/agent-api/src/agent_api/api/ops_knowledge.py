"""Ops knowledge admin: list documents, patch review_status, list snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentSnapshot,
)
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/knowledge", tags=["ops-knowledge"])

ReviewStatus = Literal["curated", "clinically_reviewed", "withdrawn"]


class KnowledgeBaseOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str


class KnowledgeBaseListResponse(BaseModel):
    bases: list[KnowledgeBaseOut]


class KnowledgeDocumentOut(BaseModel):
    id: UUID
    slug: str
    title: str
    source_kind: str
    source_url: str | None
    source_label: str | None
    source_date: str | None
    version_label: str | None
    review_status: str
    reviewed_at: datetime | None
    chunk_count: int


class KnowledgeDocumentListResponse(BaseModel):
    documents: list[KnowledgeDocumentOut]


class PatchReviewRequest(BaseModel):
    review_status: ReviewStatus


class SnapshotOut(BaseModel):
    id: UUID
    version_label: str | None
    created_at: datetime
    created_by: str


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotOut]


@router.get("/bases", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> KnowledgeBaseListResponse:
    async with session_factory() as session:
        rows = list(await session.scalars(select(KnowledgeBase).order_by(KnowledgeBase.slug)))
    return KnowledgeBaseListResponse(
        bases=[
            KnowledgeBaseOut(id=row.id, slug=row.slug, name=row.name, status=row.status)
            for row in rows
        ],
    )


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def list_knowledge_documents(
    _subject: Annotated[str, Depends(get_ops_subject)],
    base: Annotated[str, Query(min_length=1, max_length=64)] = "mma-pa",
) -> KnowledgeDocumentListResponse:
    async with session_factory() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == base))
        if kb is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
            )

        chunk_count = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == KnowledgeDocument.id)
            .correlate(KnowledgeDocument)
            .scalar_subquery()
        )
        result = await session.execute(
            select(KnowledgeDocument, chunk_count)
            .where(KnowledgeDocument.knowledge_base_id == kb.id)
            .order_by(KnowledgeDocument.slug),
        )
        rows = result.all()

    return KnowledgeDocumentListResponse(
        documents=[
            KnowledgeDocumentOut(
                id=doc.id,
                slug=doc.slug,
                title=doc.title,
                source_kind=doc.source_kind,
                source_url=doc.source_url,
                source_label=doc.source_label,
                source_date=doc.source_date,
                version_label=doc.version_label,
                review_status=doc.review_status,
                reviewed_at=doc.reviewed_at,
                chunk_count=int(count or 0),
            )
            for doc, count in rows
        ],
    )


@router.patch("/documents/{document_id}", response_model=KnowledgeDocumentOut)
async def patch_knowledge_document(
    document_id: UUID,
    payload: PatchReviewRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> KnowledgeDocumentOut:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        document.review_status = payload.review_status
        document.reviewed_at = now
        await session.flush()
        count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document.id),
        )
        out = KnowledgeDocumentOut(
            id=document.id,
            slug=document.slug,
            title=document.title,
            source_kind=document.source_kind,
            source_url=document.source_url,
            source_label=document.source_label,
            source_date=document.source_date,
            version_label=document.version_label,
            review_status=document.review_status,
            reviewed_at=document.reviewed_at,
            chunk_count=int(count or 0),
        )
    return out


@router.get("/documents/{document_id}/snapshots", response_model=SnapshotListResponse)
async def list_document_snapshots(
    document_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> SnapshotListResponse:
    async with session_factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        rows = list(
            await session.scalars(
                select(KnowledgeDocumentSnapshot)
                .where(KnowledgeDocumentSnapshot.document_id == document_id)
                .order_by(KnowledgeDocumentSnapshot.created_at.desc()),
            ),
        )
    return SnapshotListResponse(
        snapshots=[
            SnapshotOut(
                id=row.id,
                version_label=row.version_label,
                created_at=row.created_at,
                created_by=row.created_by,
            )
            for row in rows
        ],
    )
