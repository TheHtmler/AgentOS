from __future__ import annotations

from typing import Any, cast

from agent_api.knowledge.chunking import chunk_text
from agent_api.knowledge.types import ChunkSpec, DocumentSpec


def document_payloads_from_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
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


def document_spec_from_payload(payload: dict[str, Any]) -> DocumentSpec:
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError(f"knowledge document {payload['slug']} chunks must be a list")
    chunk_rows = cast(list[dict[str, Any]], chunks)
    return DocumentSpec(
        slug=str(payload["slug"]),
        title=str(payload["title"]),
        chunks=[
            ChunkSpec(
                chunk_index=int(row["chunk_index"]),
                title=str(row["title"]),
                content=str(row["content"]),
                section_label=row.get("section_label"),
                tags=list(row.get("tags") or []),
            )
            for row in chunk_rows
        ],
        source_url=payload.get("source_url"),
        source_label=payload.get("source_label"),
        source_kind=str(payload.get("source_kind", "curated_summary")),
        source_date=payload.get("source_date"),
        version_label=payload.get("version_label"),
        review_status=str(payload.get("review_status", "curated")),
    )


def normalize_json_payload(payload: dict[str, Any]) -> list[DocumentSpec]:
    return [
        document_spec_from_payload(document_payload)
        for document_payload in document_payloads_from_json(payload)
    ]


def normalize_plain_text(
    *,
    slug: str,
    title: str,
    body: str,
    **source_fields: Any,
) -> DocumentSpec:
    normalized_slug = slug.strip()
    if not normalized_slug:
        raise ValueError("slug is required")

    return DocumentSpec(
        slug=normalized_slug,
        title=title,
        chunks=chunk_text(body),
        source_url=source_fields.get("source_url"),
        source_label=source_fields.get("source_label"),
        source_kind=str(source_fields.get("source_kind", "imported_text")),
        source_date=source_fields.get("source_date"),
        version_label=source_fields.get("version_label"),
        review_status=str(source_fields.get("review_status", "draft")),
    )
