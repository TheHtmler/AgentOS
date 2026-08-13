"""Upsert helpers for curated public knowledge bases."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentSnapshot,
)
from agent_api.memory.embed import embed_text

logger = logging.getLogger(__name__)

SERVICE_ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = SERVICE_ROOT / "seed" / "knowledge" / "mma_pa_chunks.json"

_NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
KNOWLEDGE_BASE_ID = uuid5(_NS, "agentos.knowledge.mma-pa")


def document_id_for_slug(document_slug: str) -> UUID:
    if document_slug == "mma-pa-core-v1":
        # Preserve the UUID used by the original single-document seed.
        return uuid5(_NS, "agentos.knowledge.mma-pa.mma-pa-core-v1")
    return uuid5(_NS, f"agentos.knowledge.mma-pa.document.{document_slug}")


DOCUMENT_ID = document_id_for_slug("mma-pa-core-v1")


def chunk_id_for_index(chunk_index: int, document_slug: str = "mma-pa-core-v1") -> UUID:
    if document_slug == "mma-pa-core-v1":
        # Preserve chunk IDs used by the original single-document seed.
        return uuid5(_NS, f"agentos.knowledge.mma-pa.chunk.{chunk_index}")
    return uuid5(
        _NS,
        f"agentos.knowledge.mma-pa.document.{document_slug}.chunk.{chunk_index}",
    )


def load_mma_pa_seed(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SEED_PATH).read_text(encoding="utf-8"))


def _document_specs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the original single-document seed and the multi-document format."""

    documents = payload.get("documents")
    if documents is None:
        document = dict(payload["document"])
        document["chunks"] = payload["chunks"]
        return [document]
    if not isinstance(documents, list) or not documents:
        raise ValueError("knowledge seed documents must be a non-empty list")
    document_items = cast(list[object], documents)
    if not all(isinstance(item, dict) for item in document_items):
        raise ValueError("knowledge seed documents must contain objects")
    return [cast(dict[str, Any], item) for item in document_items]


async def upsert_mma_pa_knowledge(
    session: AsyncSession,
    seed: dict[str, Any] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    """Replace MMA/PA seed document chunks; return chunk count.

    When ``http_client`` is provided and knowledge embeddings are enabled, each
    chunk is embedded at seed time (failures leave embedding=null).
    """

    payload = seed if seed is not None else load_mma_pa_seed()
    base_spec = payload["knowledge_base"]
    document_specs = _document_specs(payload)
    settings = get_settings()
    embed_enabled = http_client is not None and settings.knowledge_embedding_enabled

    base = await session.get(KnowledgeBase, KNOWLEDGE_BASE_ID)
    if base is None:
        session.add(
            KnowledgeBase(
                id=KNOWLEDGE_BASE_ID,
                slug=base_spec["slug"],
                name=base_spec["name"],
                description=base_spec.get("description"),
                status="active",
            ),
        )
    else:
        base.slug = base_spec["slug"]
        base.name = base_spec["name"]
        base.description = base_spec.get("description")
        base.status = "active"

    document_ids: list[UUID] = []
    embedded = 0
    total_chunks = 0
    for doc_spec in document_specs:
        document_slug = str(doc_spec["slug"])
        document_id = document_id_for_slug(document_slug)
        document_ids.append(document_id)
        document = await session.get(KnowledgeDocument, document_id)
        fields = {
            "knowledge_base_id": KNOWLEDGE_BASE_ID,
            "slug": document_slug,
            "title": str(doc_spec["title"]),
            "source_url": doc_spec.get("source_url"),
            "source_label": doc_spec.get("source_label"),
            "source_kind": doc_spec.get("source_kind", "curated_summary"),
            "source_date": doc_spec.get("source_date"),
            "version_label": doc_spec.get("version_label"),
            "review_status": doc_spec.get("review_status", "curated"),
        }
        if document is None:
            document = KnowledgeDocument(id=document_id, **fields)
            session.add(document)
        else:
            existing_chunks = list(
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document_id)
                    .order_by(KnowledgeChunk.chunk_index),
                ),
            )
            if existing_chunks:
                session.add(
                    KnowledgeDocumentSnapshot(
                        document_id=document_id,
                        version_label=document.version_label,
                        payload={
                            "document": {
                                "slug": document.slug,
                                "title": document.title,
                                "source_url": document.source_url,
                                "source_label": document.source_label,
                                "source_kind": document.source_kind,
                                "source_date": document.source_date,
                                "version_label": document.version_label,
                                "review_status": document.review_status,
                            },
                            "chunks": [
                                {
                                    "chunk_index": chunk.chunk_index,
                                    "title": chunk.title,
                                    "content": chunk.content,
                                    "section_label": chunk.section_label,
                                    "tags": list(chunk.tags or []),
                                }
                                for chunk in existing_chunks
                            ],
                        },
                        created_by="system",
                    ),
                )
            for key, value in fields.items():
                setattr(document, key, value)

        chunks_spec = doc_spec.get("chunks")
        if not isinstance(chunks_spec, list):
            raise ValueError(f"knowledge document {document_slug} chunks must be a list")
        chunk_rows = cast(list[dict[str, Any]], chunks_spec)
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id),
        )
        for row in chunk_rows:
            title = str(row["title"])
            content = str(row["content"])
            embedding: list[float] | None = None
            if embed_enabled and http_client is not None:
                embedding = await embed_text(
                    f"{title}\n{content}",
                    http_client,
                    settings=settings,
                    enabled=True,
                )
                if embedding is not None:
                    embedded += 1
            session.add(
                KnowledgeChunk(
                    id=chunk_id_for_index(int(row["chunk_index"]), document_slug),
                    document_id=document_id,
                    chunk_index=int(row["chunk_index"]),
                    title=title,
                    content=content,
                    section_label=row.get("section_label"),
                    tags=list(row.get("tags") or []),
                    embedding=embedding,
                ),
            )
            total_chunks += 1

    await session.execute(
        delete(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == KNOWLEDGE_BASE_ID,
            KnowledgeDocument.id.notin_(document_ids),
        ),
    )

    clash = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.slug == base_spec["slug"],
            KnowledgeBase.id != KNOWLEDGE_BASE_ID,
        ),
    )
    if clash is not None:
        raise RuntimeError(f"knowledge base slug conflict: {base_spec['slug']}")

    if embed_enabled:
        logger.info(
            "seeded knowledge embeddings: %s/%s chunks",
            embedded,
            total_chunks,
        )
    return total_chunks
