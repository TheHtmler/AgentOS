"""Background execution for Ops knowledge imports.

The import HTTP request only validates input and flips the document to
``processing``; a per-document asyncio task then runs extract → batch-embed →
persist, with the final transaction holding no slow HTTP calls. Re-submitting
a slug that is already processing dedups onto the in-flight job instead of
starting a duplicate — the document row's ``import_status`` is the
authoritative gate, made race-safe by the same advisory lock persist uses.
HTTP clients are app-lifetime pools captured at submit, safe to use after the
response returns.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy.exc import IntegrityError

from agent_api.config import get_settings
from agent_api.db.knowledge_store import (
    document_id_for_slug,
    fail_import,
    mark_interrupted_imports_failed,
    prepare_document_for_import,
    set_import_progress,
    upsert_knowledge_document,
)
from agent_api.db.session import session_factory
from agent_api.knowledge.types import DocumentSpec
from agent_api.knowledge.vision_extract import VisionExtractError
from agent_api.memory.embed import embed_texts

logger = logging.getLogger(__name__)

# slug → running task; kept only for shutdown cancellation. Dedup decisions
# always read the document row, never this dict.
_INFLIGHT: dict[str, asyncio.Task[None]] = {}

ProgressFn = Callable[[int, int], Awaitable[None]]
# (spec, vision_pages, text_layer_fallback_pages)
ExtractResult = tuple[DocumentSpec, int, int]
ExtractFn = Callable[[ProgressFn], Awaitable[ExtractResult]]


@dataclass(frozen=True)
class SubmittedImport:
    document_id: UUID
    slug: str
    title: str
    overwrote: bool
    already_processing: bool


def static_extract(spec: DocumentSpec) -> ExtractFn:
    """Wrap an already-normalized spec (text/JSON modes) as an ExtractFn."""

    async def run(_on_progress: ProgressFn) -> ExtractResult:
        return spec, 0, 0

    return run


async def start_import(
    *,
    base_slug: str,
    slug: str,
    title: str,
    created_by: str,
    extract: ExtractFn,
    embedding_client: httpx.AsyncClient | None,
) -> SubmittedImport:
    """Flip the document to ``processing`` and spawn its import task.

    Idempotent: a document already processing returns the in-flight submission
    instead of starting a duplicate task.
    """

    async with session_factory() as session, session.begin():
        document, already_processing, existed = await prepare_document_for_import(
            session,
            base_slug=base_slug,
            slug=slug,
            title=title,
        )
        document_id = document.id
        document_title = document.title

    if already_processing:
        return SubmittedImport(
            document_id=document_id,
            slug=slug,
            title=document_title,
            overwrote=True,
            already_processing=True,
        )

    task = asyncio.create_task(
        _run_import(
            base_slug=base_slug,
            slug=slug,
            created_by=created_by,
            extract=extract,
            embedding_client=embedding_client,
        ),
        name=f"knowledge-import-{slug}",
    )
    _INFLIGHT[slug] = task

    def forget(completed: asyncio.Task[None]) -> None:
        if _INFLIGHT.get(slug) is completed:
            _INFLIGHT.pop(slug, None)

    task.add_done_callback(forget)
    return SubmittedImport(
        document_id=document_id,
        slug=slug,
        title=document_title,
        overwrote=existed,
        already_processing=False,
    )


async def _run_import(
    *,
    base_slug: str,
    slug: str,
    created_by: str,
    extract: ExtractFn,
    embedding_client: httpx.AsyncClient | None,
) -> None:
    document_id = document_id_for_slug(slug)
    try:

        async def report_progress(done: int, total: int) -> None:
            async with session_factory() as session, session.begin():
                await set_import_progress(
                    session,
                    document_id=document_id,
                    done=done,
                    total=total,
                )

        spec, _vision_pages, _fallback_pages = await extract(report_progress)

        settings = get_settings()
        embeddings: list[list[float] | None] | None = None
        if embedding_client is not None and settings.knowledge_embedding_enabled and spec.chunks:
            # Batched, and computed BEFORE the persist transaction — per-chunk
            # calls inside the transaction used to hold it open for tens of
            # seconds, which was the window behind the duplicate-key 500s.
            embeddings = await embed_texts(
                [f"{chunk.title}\n{chunk.content}" for chunk in spec.chunks],
                embedding_client,
                settings=settings,
                enabled=True,
            )

        async with session_factory() as session, session.begin():
            await upsert_knowledge_document(
                session,
                base_slug=base_slug,
                spec=spec,
                created_by=created_by,
                embeddings=embeddings,
            )
    except asyncio.CancelledError:
        await _fail_safely(document_id_for_slug(slug), "导入被中断，请重新导入。")
        raise
    except (VisionExtractError, ValueError) as exc:
        logger.warning("knowledge import failed for %s: %s", slug, exc)
        await _fail_safely(document_id, str(exc))
    except IntegrityError:
        logger.exception("knowledge import hit a conflict for %s", slug)
        await _fail_safely(document_id, "与正在进行的相同导入冲突，请稍后重试。")
    except Exception:
        logger.exception("knowledge import failed unexpectedly for %s", slug)
        await _fail_safely(document_id, "导入失败（内部错误），请查看服务端日志。")


async def _fail_safely(document_id: UUID, message: str) -> None:
    try:
        async with session_factory() as session, session.begin():
            await fail_import(session, document_id=document_id, error=message)
    except Exception:
        logger.exception("unable to mark import failed for document %s", document_id)


async def fail_interrupted_imports() -> int:
    """Mark imports in-flight at process stop as failed; returns the count."""

    async with session_factory() as session, session.begin():
        return await mark_interrupted_imports_failed(session)


async def stop_import_jobs() -> None:
    """Cancel all running import tasks (shutdown); rows flip to failed via the
    cancellation path, and anything harder-killed is caught by the next
    startup's ``fail_interrupted_imports`` sweep."""

    tasks = list(_INFLIGHT.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
