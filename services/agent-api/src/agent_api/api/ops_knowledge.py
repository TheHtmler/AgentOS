"""Ops knowledge admin: list/detail documents, patch metadata, snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from agent_api.knowledge.import_jobs import (
    ExtractResult,
    ProgressFn,
    SubmittedImport,
    start_import,
    static_extract,
)
from agent_api.knowledge.normalize import normalize_json_payload, normalize_plain_text
from agent_api.knowledge.types import ChunkSpec, DocumentSpec
from agent_api.knowledge.url_extract import fetch_url_text
from agent_api.knowledge.vision_extract import (
    VisionExtractError,
    extract_image_text_vision,
    extract_pdf_text_vision,
)
from agent_api.runtime import AgentRuntime

router = APIRouter(prefix="/v1/ops/knowledge", tags=["ops-knowledge"])

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_JSON_SUFFIXES = {".json"}

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
    # Background import lifecycle; 'ready' for settled documents.
    import_status: str
    import_error: str | None
    import_progress_done: int | None
    import_progress_total: int | None


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
    # Pages/images the background vision model transcribed (field name kept for
    # Ops frontend compatibility; no longer "OCR" — see knowledge/vision_extract.py).
    ocr_pages: int
    # PDF pages where the vision call failed and the raw PyMuPDF text layer was used instead.
    text_layer_pages: int
    # Imports run in the background — the submission response is always
    # "processing"; poll GET /documents until it turns ready/failed.
    import_status: str = "processing"


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
        import_status=doc.import_status,
        import_error=doc.import_error,
        import_progress_done=doc.import_progress_done,
        import_progress_total=doc.import_progress_total,
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
    """Derive a document slug from an uploaded filename.

    The normalization strips CJK (and other non-ASCII) characters, so names
    differing only in those characters would collapse onto one slug and
    silently overwrite each other. Whenever normalization changes the stem,
    append a short deterministic hash of it: re-uploading the same file still
    derives the same slug (overwrite semantics preserved), while near-identical
    names stay distinct documents.
    """

    stem = Path(filename or "imported-document").stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    if slug == stem:
        return stem
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:6]
    return f"{slug or 'imported-document'}-{digest}"


def _background_http_client(request: Request) -> httpx.AsyncClient | None:
    """Return the app-wide authenticated embeddings client, if the runtime is up.

    OCR/URL-fetch calls use their own short-lived, differently-authenticated
    ``httpx.AsyncClient`` — only ``runtime.background_http_client`` carries the
    ``Authorization: Bearer`` header the embeddings endpoint needs. Missing runtime
    (e.g. import-route tests that don't set ``app.state.runtime``) degrades to no
    embeddings rather than failing the import, matching ``knowledge_embedding_enabled``'s
    existing graceful fallback to keyword-only search.
    """

    runtime = getattr(request.app.state, "runtime", None)
    if isinstance(runtime, AgentRuntime):
        return runtime.background_http_client
    return None


def _background_vision_http_client(request: Request) -> httpx.AsyncClient | None:
    """Vision-import client: the dedicated endpoint when configured, else the shared one.

    Only the transcription step uses this; the embedding step in ``_persist_import``
    keeps the shared background client, so parsed text is always vectorized by the
    fixed embedding endpoint even when vision lives on a different gateway.
    """

    runtime = getattr(request.app.state, "runtime", None)
    if isinstance(runtime, AgentRuntime):
        return runtime.background_vision_http_client or runtime.background_http_client
    return None


def _submitted_out(submitted: SubmittedImport) -> ImportDocumentOut:
    """The immediate acknowledgement for one accepted import job."""

    return ImportDocumentOut(
        id=submitted.document_id,
        slug=submitted.slug,
        title=submitted.title,
        # Counts/pages are unknown until the background job lands; poll the
        # documents list for the terminal state.
        chunk_count=0,
        overwrote=submitted.overwrote,
        ocr_pages=0,
        text_layer_pages=0,
        import_status="processing",
    )


async def _submit_specs(
    request: Request,
    specs: list[DocumentSpec],
    *,
    base_slug: str,
    subject: str,
) -> ImportResponse:
    """Accept already-normalized specs as background import jobs, one per document."""

    embedding_client = _background_http_client(request)
    documents: list[ImportDocumentOut] = []
    for spec in specs:
        submitted = await start_import(
            base_slug=base_slug,
            slug=spec.slug,
            title=spec.title,
            created_by=subject,
            extract=static_extract(spec),
            embedding_client=embedding_client,
        )
        documents.append(_submitted_out(submitted))
    return ImportResponse(documents=documents)


async def _import_json_body(
    request: Request,
    payload: dict[str, Any],
    subject: str,
) -> ImportResponse:
    mode = _required_text(payload, "mode")
    base_slug = str(payload.get("base") or "mma-pa")
    if mode == "json":
        document_payload = payload.get("payload")
        if not isinstance(document_payload, dict):
            raise ValueError("payload must be an object")
        specs = normalize_json_payload(cast(dict[str, Any], document_payload))
        return await _submit_specs(request, specs, base_slug=base_slug, subject=subject)
    if mode == "text":
        spec = normalize_plain_text(
            slug=_required_text(payload, "slug"),
            title=_required_text(payload, "title"),
            body=_required_text(payload, "body"),
        )
        return await _submit_specs(request, [spec], base_slug=base_slug, subject=subject)
    if mode == "url":
        url = _required_text(payload, "url")
        slug = _required_text(payload, "slug")
        provided_title = str(payload.get("title") or "").strip()
        settings = get_settings()

        async def extract_url(_on_progress: ProgressFn) -> ExtractResult:
            # Fetch happens inside the background job, not the request — slow
            # pages no longer hold the HTTP connection.
            async with httpx.AsyncClient(timeout=settings.fetch_url_timeout_seconds) as client:
                extracted_title, body = await fetch_url_text(
                    url,
                    client=client,
                    max_bytes=settings.knowledge_import_max_bytes,
                )
            title = provided_title or extracted_title.strip()
            spec = normalize_plain_text(
                slug=slug,
                title=title,
                body=body,
                source_url=url,
                source_label=extracted_title,
            )
            return spec, 0, 0

        submitted = await start_import(
            base_slug=base_slug,
            slug=slug,
            title=provided_title or slug,
            created_by=subject,
            extract=extract_url,
            embedding_client=_background_http_client(request),
        )
        return ImportResponse(documents=[_submitted_out(submitted)])
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
    filename = (upload.filename or "").lower()
    suffix = Path(filename).suffix
    mime = (upload.content_type or "").lower()
    is_pdf = mode == "pdf" or mime == "application/pdf" or suffix == ".pdf"
    is_image = mime in _IMAGE_TYPES or suffix in _IMAGE_SUFFIXES
    is_json = mime == "application/json" or suffix in _JSON_SUFFIXES
    is_text = mime.startswith("text/") or suffix in _TEXT_SUFFIXES

    if is_image or is_pdf:
        vision_client = _background_vision_http_client(request)
        if vision_client is None or not settings.resolved_background_vision_model:
            raise ValueError(
                "PDF/图片导入需要先配置 BACKGROUND_VISION_MODEL（及 BACKGROUND_BASE_URL）。",
            )

        if is_image:

            async def extract_image(_on_progress: ProgressFn) -> ExtractResult:
                body = await extract_image_text_vision(
                    data,
                    http_client=vision_client,
                    settings=settings,
                )
                return normalize_plain_text(slug=slug, title=title, body=body), 1, 0

            extract = extract_image
        else:

            async def extract_pdf(on_progress: ProgressFn) -> ExtractResult:
                body, _fallback_pages, _vision_pages = await extract_pdf_text_vision(
                    data,
                    http_client=vision_client,
                    settings=settings,
                    on_progress=on_progress,
                )
                return normalize_plain_text(slug=slug, title=title, body=body), 0, 0

            extract = extract_pdf

        submitted = await start_import(
            base_slug=base_slug,
            slug=slug,
            title=title,
            created_by=subject,
            extract=extract,
            embedding_client=_background_http_client(request),
        )
        return ImportResponse(documents=[_submitted_out(submitted)])

    if is_json:
        payload = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        specs = normalize_json_payload(payload)
        return await _submit_specs(request, specs, base_slug=base_slug, subject=subject)

    if is_text or suffix == "":
        body = data.decode("utf-8")
        spec = normalize_plain_text(slug=slug, title=title, body=body)
        return await _submit_specs(request, [spec], base_slug=base_slug, subject=subject)

    raise ValueError("仅支持 txt、md、json、pdf、jpg、png、webp")


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
            return await _import_json_body(request, cast(dict[str, Any], payload), subject)
        if content_type.startswith("multipart/form-data"):
            return await _import_multipart(request, subject)
        raise ValueError("content type must be application/json or multipart/form-data")
    except VisionExtractError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Same-slug imports are serialized by an advisory lock in the store, but a
        # residual conflict (e.g. lock skipped by a caller) must not surface as 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="相同文档正在导入中，请等待上一次导入完成后重试。",
        ) from exc
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


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(
    document_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    async with session_factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        await session.delete(document)


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


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _object_map(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _chunk_spec(value: object) -> ChunkSpec:
    item = _object_map(value, "chunk")
    tags_raw = item.get("tags")
    tags = [str(tag) for tag in cast(list[object], tags_raw)] if isinstance(tags_raw, list) else []
    return ChunkSpec(
        chunk_index=int(item["chunk_index"]),
        title=str(item["title"]),
        content=str(item["content"]),
        section_label=_optional_str(item.get("section_label")),
        tags=tags,
    )


def _spec_from_snapshot(document: KnowledgeDocument, payload: dict[str, Any]) -> DocumentSpec:
    # Restore must keep the live document slug: upsert IDs are derived from slug.
    doc_meta = _object_map(payload.get("document"), "document")
    raw_chunks = payload.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise ValueError("snapshot payload missing chunks")

    chunks = [_chunk_spec(item) for item in cast(list[object], raw_chunks)]
    version_label = _optional_str(doc_meta.get("version_label")) or document.version_label
    return DocumentSpec(
        slug=document.slug,
        title=str(doc_meta.get("title") or document.title),
        chunks=chunks,
        source_kind=str(doc_meta.get("source_kind") or document.source_kind),
        source_url=_optional_str(doc_meta.get("source_url")),
        source_label=_optional_str(doc_meta.get("source_label")),
        source_date=_optional_str(doc_meta.get("source_date")),
        version_label=version_label,
        review_status=str(doc_meta.get("review_status") or document.review_status),
    )


@router.post(
    "/documents/{document_id}/snapshots/{snapshot_id}/restore",
    response_model=KnowledgeDocumentDetailOut,
)
async def restore_document_snapshot(
    document_id: UUID,
    snapshot_id: UUID,
    subject: Annotated[str, Depends(get_ops_subject)],
) -> KnowledgeDocumentDetailOut:
    async with session_factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        snapshot = await session.get(KnowledgeDocumentSnapshot, snapshot_id)
        if snapshot is None or snapshot.document_id != document_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
        base = await session.get(KnowledgeBase, document.knowledge_base_id)
        if base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found",
            )
        try:
            spec = _spec_from_snapshot(document, dict(snapshot.payload))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid snapshot payload: {exc}",
            ) from exc

        await upsert_knowledge_document(
            session,
            base_slug=base.slug,
            spec=spec,
            created_by=subject,
        )
        restored = await session.get(KnowledgeDocument, document_id)
        if restored is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.chunk_index),
            ),
        )
        out = KnowledgeDocumentDetailOut(
            **_document_out(restored, len(chunks)).model_dump(),
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
    return out
