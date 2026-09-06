"""Ops knowledge admin: list/detail documents, patch metadata, snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import case, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import FormData, UploadFile

from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.db.knowledge_store import (
    lock_document_import,
    upsert_knowledge_document,
)
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
from agent_api.knowledge.ontology import resolve_ontology_terms
from agent_api.knowledge.pubmed import fetch_pubmed_abstract, search_pubmed
from agent_api.knowledge.sources import archive_bytes, load_checkpoint, source_path
from agent_api.knowledge.types import ChunkSpec, DocumentSpec, OntologyTermSpec
from agent_api.knowledge.url_extract import fetch_url_text
from agent_api.knowledge.vectors import EMBEDDING_VERSION, retrieval_text, usable_vector
from agent_api.knowledge.vision_extract import (
    VisionExtractError,
    extract_image_text_vision,
    extract_pdf_text_vision,
)
from agent_api.memory.embed import embed_text, embed_texts
from agent_api.runtime import AgentRuntime

router = APIRouter(prefix="/v1/ops/knowledge", tags=["ops-knowledge"])


def _usable_vector_conditions() -> list[Any]:
    cfg = get_settings()
    return [
        KnowledgeChunk.embedding_model == cfg.resolved_background_embedding_model,
        KnowledgeChunk.embedding_version == EMBEDDING_VERSION,
        ~func.jsonb_path_exists(
            KnowledgeChunk.embedding, sql_cast('$[*] ? (@.type() != "number")', JSONPATH)
        ),
        func.jsonb_path_exists(KnowledgeChunk.embedding, sql_cast("$[*] ? (@ != 0)", JSONPATH)),
        func.jsonb_array_length(
            case(
                (func.jsonb_typeof(KnowledgeChunk.embedding) == "array", KnowledgeChunk.embedding),
                else_=None,
            )
        )
        == cfg.knowledge_embedding_dimensions,
    ]


async def _save_page_reports(slug: str, pages: list[dict[str, object]]) -> None:
    from agent_api.db.knowledge_store import document_id_for_slug

    async with session_factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, document_id_for_slug(slug))
        if document is not None:
            document.import_details = {**document.import_details, "pages": list(pages)}


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_JSON_SUFFIXES = {".json"}

ReviewStatus = Literal["pending_review", "curated", "clinically_reviewed", "withdrawn"]
SourceKind = Literal[
    "official_reference", "clinical_guideline", "curated_summary", "research_article"
]


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
    ontology_terms: list[dict[str, str]]
    review_status: str
    reviewed_at: datetime | None
    chunk_count: int
    # Background import lifecycle; 'ready' for settled documents.
    import_status: str
    import_error: str | None
    import_progress_done: int | None
    import_progress_total: int | None
    # Vector health: how many chunks actually carry an embedding for this
    # document. A document can import "successfully" while every chunk has
    # embedding=null (endpoint down, key missing) — those chunks then only
    # match keyword searches and the vector leg of hybrid search is dead.
    embedded_chunks: int = 0
    embedding_model: str | None = None
    import_stage: str | None = None
    quality: dict[str, object] = Field(default_factory=dict)
    source_available: bool = False
    vector_state: str = "missing"


class KnowledgeDocumentListResponse(BaseModel):
    documents: list[KnowledgeDocumentOut]


class KnowledgeChunkOut(BaseModel):
    id: UUID
    chunk_index: int
    title: str
    content: str
    section_label: str | None
    tags: list[str]
    # Vector health per chunk: whether an embedding exists and which model
    # produced it (null when the chunk only matches keyword searches).
    embedded: bool = False
    embedding_model: str | None = None


class KnowledgeDocumentDetailOut(KnowledgeDocumentOut):
    chunks: list[KnowledgeChunkOut]


class OntologyTermSpecIn(BaseModel):
    curie: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9._-]+$",
    )
    label: str = Field(min_length=1, max_length=256)
    ontology: str = Field(min_length=1, max_length=64)


class PatchDocumentRequest(BaseModel):
    review_status: ReviewStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    version_label: str | None = Field(default=None, max_length=128)
    source_kind: SourceKind | None = None
    source_label: str | None = Field(default=None, max_length=256)
    source_url: str | None = None
    source_date: str | None = Field(default=None, max_length=32)
    ontology_terms: list[OntologyTermSpecIn] | None = None

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


class PubMedArticleOut(BaseModel):
    pmid: str
    title: str
    journal: str | None
    publication_date: str | None
    authors: str | None


class PubMedSearchResponse(BaseModel):
    articles: list[PubMedArticleOut]


class OntologyCandidateOut(BaseModel):
    curie: str
    label: str
    ontology: str


class OntologyResolveResponse(BaseModel):
    terms: list[OntologyCandidateOut]


def _document_out(
    doc: KnowledgeDocument,
    chunk_count: int,
    embedded_chunks: int = 0,
    embedding_model: str | None = None,
) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=doc.id,
        slug=doc.slug,
        title=doc.title,
        source_kind=doc.source_kind,
        source_url=doc.source_url,
        source_label=doc.source_label,
        source_date=doc.source_date,
        version_label=doc.version_label,
        ontology_terms=list(doc.ontology_terms or []),
        review_status=doc.review_status,
        reviewed_at=doc.reviewed_at,
        chunk_count=chunk_count,
        import_status=doc.import_status,
        import_error=doc.import_error,
        import_progress_done=doc.import_progress_done,
        import_progress_total=doc.import_progress_total,
        embedded_chunks=embedded_chunks,
        embedding_model=embedding_model,
        import_stage=doc.import_stage,
        quality={
            key: value
            for key, value in {**(doc.ingestion or {}), **(doc.import_details or {})}.items()
            if key in ("pages", "parser_version", "embedded", "chunks")
        },
        source_available=bool(
            (doc.ingestion or {}).get("source_digest")
            or (doc.import_details or {}).get("source_digest")
        ),
        vector_state="complete"
        if chunk_count and embedded_chunks == chunk_count
        else "partial"
        if embedded_chunks
        else "missing",
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
    if mode == "pubmed":
        pmid = _required_text(payload, "pmid")
        if not pmid.isdigit() or len(pmid) > 16:
            raise ValueError("pmid must be a numeric PubMed identifier")
        slug = _required_text(payload, "slug")

        async def extract_pubmed(_on_progress: ProgressFn) -> ExtractResult:
            async with httpx.AsyncClient(timeout=20.0) as client:
                article = await fetch_pubmed_abstract(pmid, client)
            source_label = (
                " · ".join(value for value in (article.journal, article.authors) if value)
                or "PubMed"
            )
            spec = normalize_plain_text(
                slug=slug,
                title=article.title,
                body=article.abstract or "",
                source_kind="research_article",
                source_url=f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/",
                source_label=source_label,
                source_date=article.publication_date,
                version_label=f"PMID:{article.pmid}",
                # Literature candidates cannot enter a family-facing corpus
                # before an Ops reviewer has checked applicability and scope.
                review_status="pending_review",
            )
            return spec, 0, 0

        submitted = await start_import(
            base_slug=base_slug,
            slug=slug,
            title=slug,
            created_by=subject,
            extract=extract_pubmed,
            embedding_client=_background_http_client(request),
        )
        return ImportResponse(documents=[_submitted_out(submitted)])
    raise ValueError(f"unsupported import mode: {mode}")


@router.get("/evidence/pubmed", response_model=PubMedSearchResponse)
async def search_pubmed_evidence(
    query: Annotated[str, Query(min_length=3, max_length=256)],
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> PubMedSearchResponse:
    # This fixed public endpoint accepts only an operator's literature query;
    # it is deliberately outside the product chat path and never sees Case data.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            articles = await search_pubmed(query, client)
    except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PubMed 检索失败",
        ) from exc
    return PubMedSearchResponse(
        articles=[
            PubMedArticleOut(
                pmid=article.pmid,
                title=article.title,
                journal=article.journal,
                publication_date=article.publication_date,
                authors=article.authors,
            )
            for article in articles
        ],
    )


@router.get("/ontology/resolve", response_model=OntologyResolveResponse)
async def resolve_knowledge_ontology(
    query: Annotated[str, Query(min_length=2, max_length=128)],
    ontology: Annotated[Literal["mondo", "hp", "ordo"], Query()],
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OntologyResolveResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            terms = await resolve_ontology_terms(query, ontology, client)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="术语服务暂不可用",
        ) from exc
    return OntologyResolveResponse(
        terms=[
            OntologyCandidateOut(curie=term.curie, label=term.label, ontology=term.ontology)
            for term in terms
        ],
    )


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
    source_info: dict[str, object] = {
        "source_digest": archive_bytes(data),
        "filename": Path(upload.filename or "document").name,
        "source_type": "pdf" if is_pdf else "image" if is_image else "text",
    }

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
                spec = normalize_plain_text(slug=slug, title=title, body=body)
                spec.ingestion.update(source_info)
                return spec, 1, 0

            extract = extract_image
        else:

            async def extract_pdf(on_progress: ProgressFn) -> ExtractResult:
                pages: list[dict[str, object]] = []

                async def progress(done: int, total: int) -> None:
                    await on_progress(done, total)
                    await _save_page_reports(slug, pages)

                body, fallback_pages, vision_pages = await extract_pdf_text_vision(
                    data,
                    http_client=vision_client,
                    settings=settings,
                    on_progress=progress,
                    page_reports=pages,
                )
                spec = normalize_plain_text(slug=slug, title=title, body=body)
                spec.ingestion.update({**source_info, "pages": pages})
                if fallback_pages:
                    spec.review_status = "pending_review"
                return spec, vision_pages, fallback_pages

            extract = extract_pdf

        submitted = await start_import(
            base_slug=base_slug,
            slug=slug,
            title=title,
            created_by=subject,
            extract=extract,
            embedding_client=_background_http_client(request),
            details=source_info,
        )
        return ImportResponse(documents=[_submitted_out(submitted)])

    if is_json:
        payload = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        specs = normalize_json_payload(payload)
        return await _submit_specs(request, specs, base_slug=base_slug, subject=subject)

    if is_text or suffix == "":
        body = data.decode("utf-8")
        spec = normalize_plain_text(slug=slug, title=title, body=body)
        spec.ingestion.update(source_info)
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
        embedded_count = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id == KnowledgeDocument.id,
                *_usable_vector_conditions(),
            )
            .correlate(KnowledgeDocument)
            .scalar_subquery()
        )
        # All embedded chunks of one document share the model that produced
        # them; any non-null value names it (null when nothing is embedded).
        embedding_model = (
            select(func.max(KnowledgeChunk.embedding_model))
            .where(KnowledgeChunk.document_id == KnowledgeDocument.id)
            .correlate(KnowledgeDocument)
            .scalar_subquery()
        )
        result = await session.execute(
            select(KnowledgeDocument, chunk_count, embedded_count, embedding_model)
            .where(KnowledgeDocument.knowledge_base_id == kb.id)
            .order_by(KnowledgeDocument.slug),
        )
        rows = result.all()

    return KnowledgeDocumentListResponse(
        documents=[
            _document_out(
                doc,
                int(count or 0),
                embedded_chunks=int(embedded or 0),
                embedding_model=model,
            )
            for doc, count, embedded, model in rows
        ],
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
        embedded_chunks = sum(
            usable_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_version)
            for chunk in chunks
        )
        embedding_model = next(
            (chunk.embedding_model for chunk in chunks if chunk.embedding_model is not None),
            None,
        )
        base = _document_out(
            document,
            len(chunks),
            embedded_chunks=embedded_chunks,
            embedding_model=embedding_model,
        )
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
                embedded=usable_vector(
                    chunk.embedding, chunk.embedding_model, chunk.embedding_version
                ),
                embedding_model=chunk.embedding_model,
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
        await lock_document_import(session, document_id)
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        if document.import_status == "processing":
            raise HTTPException(409, "导入进行中，请完成后修改")

        if "review_status" in updates:
            document.review_status = updates["review_status"]
            document.reviewed_at = now
        if "title" in updates and updates["title"] is not None:
            if updates["title"] != document.title:
                from sqlalchemy import update

                await session.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document_id)
                    .values(embedding_version=None)
                )
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
        if "ontology_terms" in updates and updates["ontology_terms"] is not None:
            terms = payload.ontology_terms
            assert terms is not None
            prior_curies = {
                term["curie"]
                for term in document.ontology_terms or []
                if isinstance(term.get("curie"), str)
            }
            document.ontology_terms = [term.model_dump() for term in terms]
            current_curies = [term.curie for term in terms]
            chunks = list(
                await session.scalars(
                    select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id),
                ),
            )
            for chunk in chunks:
                retained = [tag for tag in chunk.tags if tag not in prior_curies]
                chunk.tags = list(dict.fromkeys([*retained, *current_curies]))

        await session.flush()
        chunk_count = await _chunk_count(session, document.id)
        embedded_chunks = int(
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(
                    KnowledgeChunk.document_id == document.id,
                    *_usable_vector_conditions(),
                ),
            )
            or 0,
        )
        embedding_model = await session.scalar(
            select(func.max(KnowledgeChunk.embedding_model)).where(
                KnowledgeChunk.document_id == document.id,
            ),
        )
        out = _document_out(
            document,
            chunk_count,
            embedded_chunks=embedded_chunks,
            embedding_model=embedding_model,
        )
    return out


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(
    document_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    async with session_factory() as session, session.begin():
        await lock_document_import(session, document_id)
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        if document.import_status == "processing":
            raise HTTPException(409, "导入进行中，请完成后删除")
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
        ontology_terms=[
            OntologyTermSpec(**term)
            for term in cast(list[dict[str, str]], doc_meta.get("ontology_terms") or [])
        ],
        ingestion=dict(doc_meta.get("ingestion") or {}),
    )


class RebuildRequest(BaseModel):
    mode: Literal["vectors", "retry", "reparse"] = "vectors"


class SearchDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    agent_slug: str = Field(default="imd", min_length=1, max_length=128)
    disease_tags: str = Field(default="", max_length=500)
    max_results: int = Field(default=5, ge=1, le=8)


@router.post("/search")
async def debug_knowledge_search(
    payload: SearchDebugRequest,
    request: Request,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> dict[str, Any]:
    import time

    from agent_api.db.models import Agent, AgentVersion
    from agent_api.tools.knowledge.tool import (
        parse_disease_tags,
        search_knowledge_chunks,
    )

    started = time.monotonic()
    cfg = get_settings()
    client = _background_http_client(request)
    vector = (
        await embed_text(
            payload.query, client, settings=cfg, enabled=cfg.knowledge_embedding_enabled
        )
        if client
        else None
    )
    diagnostics: dict[str, Any] = {}
    async with session_factory() as session:
        version = await session.scalar(
            select(AgentVersion)
            .join(Agent)
            .where(Agent.slug == payload.agent_slug, AgentVersion.is_published.is_(True))
        )
        if version is None:
            raise HTTPException(404, "Published agent not found")
        hits = await search_knowledge_chunks(
            session,
            query=payload.query,
            disease_tags=parse_disease_tags(payload.disease_tags),
            max_results=payload.max_results,
            knowledge_base_slugs=version.knowledge_base_slugs,
            query_embedding=vector,
            current_embedding_model=cfg.resolved_background_embedding_model,
            diagnostics=diagnostics,
        )
    return {
        "results": hits,
        "diagnostics": diagnostics,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "query_embedding": vector is not None,
    }


@router.get("/documents/{document_id}/source")
async def download_source(document_id: UUID, _subject: Annotated[str, Depends(get_ops_subject)]):
    from fastapi.responses import FileResponse

    async with session_factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        metadata = (
            document.ingestion
            if document.ingestion.get("source_digest")
            else document.import_details
        )
    try:
        path = source_path(str(metadata.get("source_digest", "")))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path,
        filename=Path(str(metadata.get("filename", "source.txt"))).name,
        media_type="application/octet-stream",
    )


@router.post("/documents/{document_id}/rebuild", response_model=ImportResponse)
async def rebuild_document(
    document_id: UUID,
    payload: RebuildRequest,
    request: Request,
    subject: Annotated[str, Depends(get_ops_subject)],
) -> ImportResponse:
    async with session_factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        base = await session.get(KnowledgeBase, document.knowledge_base_id)
        if base is None:
            raise HTTPException(404, "Knowledge base not found")
        chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
        spec = DocumentSpec(
            slug=document.slug,
            title=document.title,
            chunks=[
                ChunkSpec(c.chunk_index, c.title, c.content, c.section_label, list(c.tags))
                for c in chunks
            ],
            source_kind=document.source_kind,
            source_url=document.source_url,
            source_label=document.source_label,
            source_date=document.source_date,
            version_label=document.version_label,
            review_status=document.review_status,
            ontology_terms=[OntologyTermSpec(**term) for term in document.ontology_terms],
            ingestion=dict(document.ingestion),
        )
        details = dict(document.import_details)
        base_slug = base.slug
    try:
        if payload.mode == "retry" and details.get("checkpoint"):
            spec = load_checkpoint(str(details["checkpoint"]))
            extract = static_extract(spec)
        elif payload.mode == "vectors":
            if not spec.chunks:
                raise ValueError("没有可向量化的正文，请重试解析或重新上传")
            extract = static_extract(spec)
        else:
            metadata = (
                {**spec.ingestion, **details}
                if payload.mode == "retry" or not spec.ingestion.get("source_digest")
                else dict(spec.ingestion)
            )
            # A fresh parse must not leave an older extraction checkpoint as
            # the next retry target when the new parse fails before embedding.
            details.pop("checkpoint", None)
            data = source_path(str(metadata.get("source_digest", ""))).read_bytes()

            async def extract_source(progress: ProgressFn) -> ExtractResult:
                pages: list[dict[str, object]] = []
                cfg = get_settings()
                client = _background_vision_http_client(request)
                kind = metadata.get("source_type", "text")
                if kind in ("pdf", "image") and (
                    client is None or not cfg.resolved_background_vision_model
                ):
                    raise ValueError("未配置视觉模型")
                if kind == "pdf" and client is not None:

                    async def report(done: int, total: int) -> None:
                        await progress(done, total)
                        await _save_page_reports(spec.slug, pages)

                    body, fallback, _ = await extract_pdf_text_vision(
                        data,
                        http_client=client,
                        settings=cfg,
                        on_progress=report,
                        page_reports=pages,
                    )
                    if fallback:
                        spec.review_status = "pending_review"
                elif kind == "image" and client is not None:
                    body = await extract_image_text_vision(data, http_client=client, settings=cfg)
                else:
                    body = data.decode("utf-8")
                from agent_api.knowledge.chunking import chunk_text

                spec.chunks = chunk_text(body)
                spec.ingestion = {
                    **metadata,
                    "text": body,
                    "pages": pages,
                    "parser_version": "structure-v2",
                }
                return spec, 0, 0

            extract = extract_source
        submitted = await start_import(
            base_slug=base_slug,
            slug=spec.slug,
            title=spec.title,
            created_by=subject,
            extract=extract,
            embedding_client=_background_http_client(request),
            details=details,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return ImportResponse(documents=[_submitted_out(submitted)])


@router.post(
    "/documents/{document_id}/snapshots/{snapshot_id}/restore",
    response_model=KnowledgeDocumentDetailOut,
)
async def restore_document_snapshot(
    document_id: UUID,
    snapshot_id: UUID,
    request: Request,
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

        if document.import_status == "processing":
            raise HTTPException(409, "导入进行中，请完成后恢复")
        base_slug = base.slug
        expected_update = document.updated_at

    settings = get_settings()
    embeddings: list[list[float] | None] | None = None
    embedding_client = _background_http_client(request)
    if embedding_client is not None and settings.knowledge_embedding_enabled and spec.chunks:
        embeddings = await embed_texts(
            [
                retrieval_text(spec.title, chunk.section_label, chunk.title, chunk.content)
                for chunk in spec.chunks
            ],
            embedding_client,
            settings=settings,
            enabled=True,
        )

    async with session_factory() as session, session.begin():
        await lock_document_import(session, document_id)
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        if document.updated_at != expected_update or document.import_status == "processing":
            raise HTTPException(409, "文档已变化，请刷新后重试")
        from agent_api.knowledge.vectors import valid_vector

        if (
            embedding_client is not None
            and settings.knowledge_embedding_enabled
            and (
                embeddings is None
                or any(
                    not valid_vector(v, settings.knowledge_embedding_dimensions) for v in embeddings
                )
            )
        ):
            raise HTTPException(409, "恢复向量化失败，旧版本保持不变")
        await upsert_knowledge_document(
            session,
            base_slug=base_slug,
            spec=spec,
            created_by=subject,
            embeddings=embeddings,
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
        embedded_chunks = sum(
            usable_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_version)
            for chunk in chunks
        )
        embedding_model = next(
            (chunk.embedding_model for chunk in chunks if chunk.embedding_model is not None),
            None,
        )
        out = KnowledgeDocumentDetailOut(
            **_document_out(
                restored,
                len(chunks),
                embedded_chunks=embedded_chunks,
                embedding_model=embedding_model,
            ).model_dump(),
            chunks=[
                KnowledgeChunkOut(
                    id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    title=chunk.title,
                    content=chunk.content,
                    section_label=chunk.section_label,
                    tags=list(chunk.tags or []),
                    embedded=usable_vector(
                        chunk.embedding, chunk.embedding_model, chunk.embedding_version
                    ),
                    embedding_model=chunk.embedding_model,
                )
                for chunk in chunks
            ],
        )
    return out
