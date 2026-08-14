"""Ops knowledge admin: list/detail documents, patch metadata, snapshots."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from starlette.datastructures import FormData, UploadFile

from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.db.knowledge_store import upsert_knowledge_document
from agent_api.db.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentSnapshot,
)
from agent_api.db.session import session_factory
from agent_api.knowledge.normalize import normalize_json_payload, normalize_plain_text
from agent_api.knowledge.ocr_client import OcrError
from agent_api.knowledge.pdf_extract import extract_pdf_text
from agent_api.knowledge.types import DocumentSpec
from agent_api.knowledge.url_extract import fetch_url_text

router = APIRouter(prefix="/v1/ops/knowledge", tags=["ops-knowledge"])

ReviewStatus = Literal["curated", "clinically_reviewed", "withdrawn"]
SourceKind = Literal["official_reference", "clinical_guideline", "curated_summary"]


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


class KnowledgeChunkOut(BaseModel):
    id: UUID
    chunk_index: int
    title: str
    content: str
    section_label: str | None
    tags: list[str]


class KnowledgeDocumentDetailOut(KnowledgeDocumentOut):
    chunks: list[KnowledgeChunkOut]


class PatchDocumentRequest(BaseModel):
    review_status: ReviewStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    version_label: str | None = Field(default=None, max_length=128)
    source_kind: SourceKind | None = None
    source_label: str | None = Field(default=None, max_length=256)
    source_url: str | None = None
    source_date: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> PatchDocumentRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field is required")
        return self


class SnapshotOut(BaseModel):
    id: UUID
    version_label: str | None
    created_at: datetime
    created_by: str


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotOut]


class SnapshotDetailOut(SnapshotOut):
    payload: dict[str, Any]


class ImportDocumentOut(BaseModel):
    id: UUID
    slug: str
    title: str
    chunk_count: int
    overwrote: bool
    ocr_pages: int
    text_layer_pages: int


class ImportResponse(BaseModel):
    documents: list[ImportDocumentOut]


def _document_out(doc: KnowledgeDocument, chunk_count: int) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
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
        chunk_count=chunk_count,
    )


async def _chunk_count(session: Any, document_id: UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document_id),
    )
    return int(count or 0)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _form_text(form: FormData, field: str, default: str | None = None) -> str | None:
    value = form.get(field)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    return normalized or default


def _slug_from_filename(filename: str | None) -> str:
    stem = Path(filename or "imported-document").stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or "imported-document"


async def _persist_import(
    specs: list[DocumentSpec],
    *,
    base_slug: str,
    created_by: str,
    http_client: httpx.AsyncClient | None = None,
    text_layer_pages: int = 0,
    ocr_pages: int = 0,
) -> ImportResponse:
    documents: list[ImportDocumentOut] = []
    async with session_factory() as session, session.begin():
        for spec in specs:
            document_id, chunk_count, overwrote = await upsert_knowledge_document(
                session,
                base_slug=base_slug,
                spec=spec,
                created_by=created_by,
                http_client=http_client,
            )
            documents.append(
                ImportDocumentOut(
                    id=document_id,
                    slug=spec.slug,
                    title=spec.title,
                    chunk_count=chunk_count,
                    overwrote=overwrote,
                    ocr_pages=ocr_pages,
                    text_layer_pages=text_layer_pages,
                ),
            )
    return ImportResponse(documents=documents)


async def _import_json_body(payload: dict[str, Any], subject: str) -> ImportResponse:
    mode = _required_text(payload, "mode")
    base_slug = str(payload.get("base") or "mma-pa")
    if mode == "json":
        document_payload = payload.get("payload")
        if not isinstance(document_payload, dict):
            raise ValueError("payload must be an object")
        specs = normalize_json_payload(cast(dict[str, Any], document_payload))
        return await _persist_import(specs, base_slug=base_slug, created_by=subject)
    if mode == "text":
        spec = normalize_plain_text(
            slug=_required_text(payload, "slug"),
            title=_required_text(payload, "title"),
            body=_required_text(payload, "body"),
        )
        return await _persist_import([spec], base_slug=base_slug, created_by=subject)
    if mode == "url":
        url = _required_text(payload, "url")
        slug = _required_text(payload, "slug")
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.fetch_url_timeout_seconds) as client:
            extracted_title, body = await fetch_url_text(
                url,
                client=client,
                max_bytes=settings.knowledge_import_max_bytes,
            )
            title = str(payload.get("title") or extracted_title).strip()
            spec = normalize_plain_text(
                slug=slug,
                title=title,
                body=body,
                source_url=url,
                source_label=extracted_title,
            )
            return await _persist_import(
                [spec],
                base_slug=base_slug,
                created_by=subject,
                http_client=client,
            )
    raise ValueError(f"unsupported import mode: {mode}")


async def _import_multipart(request: Request, subject: str) -> ImportResponse:
    form = await request.form()
    mode = _form_text(form, "mode")
    if mode not in {"file", "pdf"}:
        raise ValueError("multipart mode must be file or pdf")
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise ValueError("file is required")

    settings = get_settings()
    data = await upload.read(settings.knowledge_import_max_bytes + 1)
    if len(data) > settings.knowledge_import_max_bytes:
        raise ValueError(f"file exceeds {settings.knowledge_import_max_bytes} bytes")

    base_slug = _form_text(form, "base", "mma-pa") or "mma-pa"
    slug = _form_text(form, "slug") or _slug_from_filename(upload.filename)
    title = _form_text(form, "title") or Path(upload.filename or slug).stem
    is_pdf = (
        mode == "pdf"
        or upload.content_type == "application/pdf"
        or (upload.filename or "").lower().endswith(".pdf")
    )
    if not is_pdf:
        body = data.decode("utf-8")
        spec = normalize_plain_text(slug=slug, title=title, body=body)
        return await _persist_import([spec], base_slug=base_slug, created_by=subject)

    async with httpx.AsyncClient(timeout=settings.ocr_timeout_seconds) as client:
        body, text_layer_pages, ocr_pages = await extract_pdf_text(
            data,
            client=client,
            settings=settings,
        )
        spec = normalize_plain_text(slug=slug, title=title, body=body)
        return await _persist_import(
            [spec],
            base_slug=base_slug,
            created_by=subject,
            http_client=client,
            text_layer_pages=text_layer_pages,
            ocr_pages=ocr_pages,
        )


@router.post("/import", response_model=ImportResponse)
async def import_knowledge(
    request: Request,
    subject: Annotated[str, Depends(get_ops_subject)],
) -> ImportResponse:
    try:
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith("application/json"):
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return await _import_json_body(cast(dict[str, Any], payload), subject)
        if content_type.startswith("multipart/form-data"):
            return await _import_multipart(request, subject)
        raise ValueError("content type must be application/json or multipart/form-data")
    except OcrError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found",
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
        documents=[_document_out(doc, int(count or 0)) for doc, count in rows],
    )


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentDetailOut)
async def get_knowledge_document(
    document_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> KnowledgeDocumentDetailOut:
    async with session_factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.chunk_index),
            ),
        )
        base = _document_out(document, len(chunks))
    return KnowledgeDocumentDetailOut(
        **base.model_dump(),
        chunks=[
            KnowledgeChunkOut(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                title=chunk.title,
                content=chunk.content,
                section_label=chunk.section_label,
                tags=list(chunk.tags or []),
            )
            for chunk in chunks
        ],
    )


@router.patch("/documents/{document_id}", response_model=KnowledgeDocumentOut)
async def patch_knowledge_document(
    document_id: UUID,
    payload: PatchDocumentRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> KnowledgeDocumentOut:
    updates = payload.model_dump(exclude_unset=True)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        if "review_status" in updates:
            document.review_status = updates["review_status"]
            document.reviewed_at = now
        if "title" in updates and updates["title"] is not None:
            document.title = updates["title"]
        if "version_label" in updates:
            document.version_label = updates["version_label"]
        if "source_kind" in updates and updates["source_kind"] is not None:
            document.source_kind = updates["source_kind"]
        if "source_label" in updates:
            document.source_label = updates["source_label"]
        if "source_url" in updates:
            document.source_url = updates["source_url"]
        if "source_date" in updates:
            document.source_date = updates["source_date"]

        await session.flush()
        out = _document_out(document, await _chunk_count(session, document.id))
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


@router.get(
    "/documents/{document_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailOut,
)
async def get_document_snapshot(
    document_id: UUID,
    snapshot_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> SnapshotDetailOut:
    async with session_factory() as session:
        row = await session.get(KnowledgeDocumentSnapshot, snapshot_id)
        if row is None or row.document_id != document_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return SnapshotDetailOut(
        id=row.id,
        version_label=row.version_label,
        created_at=row.created_at,
        created_by=row.created_by,
        payload=dict(row.payload),
    )
