"""Upsert helpers for curated public knowledge bases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument

SERVICE_ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = SERVICE_ROOT / "seed" / "knowledge" / "mma_pa_chunks.json"

_NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
KNOWLEDGE_BASE_ID = uuid5(_NS, "agentos.knowledge.mma-pa")
DOCUMENT_ID = uuid5(_NS, "agentos.knowledge.mma-pa.mma-pa-core-v1")


def chunk_id_for_index(chunk_index: int) -> UUID:
    return uuid5(_NS, f"agentos.knowledge.mma-pa.chunk.{chunk_index}")


def load_mma_pa_seed(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SEED_PATH).read_text(encoding="utf-8"))


async def upsert_mma_pa_knowledge(
    session: AsyncSession,
    seed: dict[str, Any] | None = None,
) -> int:
    """Replace MMA/PA seed document chunks; return chunk count."""

    payload = seed if seed is not None else load_mma_pa_seed()
    base_spec = payload["knowledge_base"]
    doc_spec = payload["document"]
    chunks_spec = payload["chunks"]

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

    document = await session.get(KnowledgeDocument, DOCUMENT_ID)
    if document is None:
        session.add(
            KnowledgeDocument(
                id=DOCUMENT_ID,
                knowledge_base_id=KNOWLEDGE_BASE_ID,
                slug=doc_spec["slug"],
                title=doc_spec["title"],
                source_url=doc_spec.get("source_url"),
                source_label=doc_spec.get("source_label"),
                version_label=doc_spec.get("version_label"),
            ),
        )
    else:
        document.knowledge_base_id = KNOWLEDGE_BASE_ID
        document.slug = doc_spec["slug"]
        document.title = doc_spec["title"]
        document.source_url = doc_spec.get("source_url")
        document.source_label = doc_spec.get("source_label")
        document.version_label = doc_spec.get("version_label")

    await session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == DOCUMENT_ID),
    )
    for row in chunks_spec:
        session.add(
            KnowledgeChunk(
                id=chunk_id_for_index(int(row["chunk_index"])),
                document_id=DOCUMENT_ID,
                chunk_index=int(row["chunk_index"]),
                title=row["title"],
                content=row["content"],
                tags=list(row.get("tags") or []),
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

    return len(chunks_spec)
