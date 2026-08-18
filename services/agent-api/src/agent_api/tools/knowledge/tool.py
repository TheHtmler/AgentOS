"""Built-in hybrid knowledge search over curated disease chunks."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, cast
from uuid import UUID

from pydantic_ai import RunContext
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agent_api.db.session import session_factory
from agent_api.memory.embed import cosine_similarity, embed_text
from agent_api.rrf import reciprocal_rank_fusion
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_CAP = 24
_VECTOR_WEIGHT = 10.0
_MIN_VECTOR_KEEP = 0.28


def tokenize_query(query: str) -> list[str]:
    tokens = [token.lower() for token in _TOKEN_RE.findall(query) if len(token) >= 2]
    # Long CJK runs rarely match verbatim via ILIKE; bigrams let chunks sharing
    # terms like 甲基丙二酸 surface anyway.
    bigrams = [
        token[index : index + 2]
        for token in tokens
        if len(token) > 4 and _CJK_RE.search(token)
        for index in range(len(token) - 1)
    ]
    return list(dict.fromkeys([*tokens, *bigrams]))[:_TOKEN_CAP]


def _parse_disease_tags(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()][:16]


def _keyword_score(content: str, title: str, tags: list[str], tokens: list[str]) -> float:
    haystack = f"{title}\n{content}".lower()
    tag_blob = " ".join(tags).lower()
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 3.0
        if token in tag_blob:
            score += 2.0
    return score


def _score_components(
    *,
    content: str,
    title: str,
    tags: list[str],
    tokens: list[str],
    disease_tags: list[str],
    query_embedding: list[float] | None,
    chunk_embedding: list[float] | None,
) -> tuple[float, float]:
    """Return (keyword, vector) components (keyword includes disease-tag boost)."""

    keyword = _keyword_score(content, title, tags, tokens)
    if disease_tags:
        overlap = len(set(disease_tags) & {tag.lower() for tag in tags})
        keyword += overlap * 4.0

    vector = 0.0
    if query_embedding and chunk_embedding:
        vector = cosine_similarity(query_embedding, list(chunk_embedding))
    return keyword, vector


def score_knowledge_hit(
    *,
    content: str,
    title: str,
    tags: list[str],
    tokens: list[str],
    disease_tags: list[str],
    query_embedding: list[float] | None,
    chunk_embedding: list[float] | None,
) -> float | None:
    """Return a combined keyword+vector score, or None when the hit should be dropped.

    This combined value is a threshold/display score only. Result ORDER in
    ``search_knowledge_chunks`` comes from Reciprocal Rank Fusion over
    separate keyword/vector rankings, not from comparing this scalar across
    candidates — keyword counts and cosine similarity don't share a scale,
    so a fixed cross-weight here would just be a tuned magic number.
    """

    keyword, vector = _score_components(
        content=content,
        title=title,
        tags=tags,
        tokens=tokens,
        disease_tags=disease_tags,
        query_embedding=query_embedding,
        chunk_embedding=chunk_embedding,
    )
    if keyword <= 0 and vector < _MIN_VECTOR_KEEP:
        return None
    return keyword + vector * _VECTOR_WEIGHT


async def search_knowledge_chunks(
    session: AsyncSession,
    *,
    query: str,
    disease_tags: list[str],
    max_results: int,
    knowledge_base_slug: str | None = None,
    query_embedding: list[float] | None = None,
    current_embedding_model: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid keyword + optional embedding search over published knowledge chunks.

    ``current_embedding_model`` gates vector scoring to chunks embedded by
    that same model — left ``None`` (unit tests that don't model this
    dimension), the gate is skipped and all embeddings are used as before.
    """

    tokens = tokenize_query(query)
    if not tokens and not disease_tags and query_embedding is None:
        return []

    stmt: Select[tuple[KnowledgeChunk, KnowledgeDocument, KnowledgeBase]] = (
        select(KnowledgeChunk, KnowledgeDocument, KnowledgeBase)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .join(KnowledgeBase, KnowledgeDocument.knowledge_base_id == KnowledgeBase.id)
        .where(
            KnowledgeBase.status == "active",
            KnowledgeDocument.review_status != "withdrawn",
        )
    )
    if knowledge_base_slug:
        stmt = stmt.where(KnowledgeBase.slug == knowledge_base_slug)
    if disease_tags:
        stmt = stmt.where(KnowledgeChunk.tags.overlap(disease_tags))

    # Embeddings or explicit tags can rescue synonym queries beyond ILIKE hits.
    use_vector = query_embedding is not None
    if tokens and not use_vector and not disease_tags:
        like_clauses: list[ColumnElement[bool]] = []
        for token in tokens:
            pattern = f"%{token}%"
            like_clauses.append(KnowledgeChunk.content.ilike(pattern))
            like_clauses.append(KnowledgeChunk.title.ilike(pattern))
            like_clauses.append(
                func.array_to_string(KnowledgeChunk.tags, " ").ilike(pattern),
            )
        stmt = stmt.where(or_(*like_clauses))

    # Pull a modest candidate set, then rank in Python for predictable scoring.
    stmt = stmt.order_by(KnowledgeDocument.slug, KnowledgeChunk.chunk_index).limit(200)
    rows = (await session.execute(stmt)).all()

    keyword_candidates: list[tuple[float, dict[str, Any]]] = []
    vector_candidates: list[tuple[float, dict[str, Any]]] = []
    model_mismatches = 0
    for chunk, document, base in rows:
        tags = [str(item) for item in cast(list[Any], chunk.tags or [])]
        chunk_embedding = list(cast(list[float], chunk.embedding)) if chunk.embedding else None
        if (
            chunk_embedding is not None
            and current_embedding_model is not None
            and chunk.embedding_model not in (None, current_embedding_model)
        ):
            chunk_embedding = None
            model_mismatches += 1
        keyword, vector = _score_components(
            content=chunk.content,
            title=chunk.title,
            tags=tags,
            tokens=tokens,
            disease_tags=disease_tags,
            query_embedding=query_embedding,
            chunk_embedding=chunk_embedding,
        )
        if keyword <= 0 and vector < _MIN_VECTOR_KEEP:
            continue
        payload = {
            "chunk_id": str(chunk.id),
            "title": chunk.title,
            "content": chunk.content,
            "tags": tags,
            "document_slug": document.slug,
            "document_title": document.title,
            "source_url": document.source_url,
            "source_label": document.source_label,
            "source_kind": document.source_kind,
            "source_date": document.source_date,
            "version_label": document.version_label,
            "review_status": document.review_status,
            "section_label": chunk.section_label,
            "knowledge_base": base.slug,
            "score": round(keyword + vector * _VECTOR_WEIGHT, 4),
        }
        if keyword > 0:
            keyword_candidates.append((keyword, payload))
        if vector >= _MIN_VECTOR_KEEP:
            vector_candidates.append((vector, payload))

    if model_mismatches:
        logger.warning(
            "knowledge_search: skipping vector score for %d chunk(s) embedded by a "
            "different model (current=%s)",
            model_mismatches,
            current_embedding_model,
        )

    # RRF fuses by rank position in each signal's own ordering, not by
    # comparing keyword counts and cosine similarity on a shared scale.
    keyword_ranked = [
        payload for _, payload in sorted(keyword_candidates, key=lambda item: item[0], reverse=True)
    ]
    vector_ranked = [
        payload for _, payload in sorted(vector_candidates, key=lambda item: item[0], reverse=True)
    ]
    fused = reciprocal_rank_fusion(
        keyword_ranked,
        vector_ranked,
        key=lambda payload: cast(str, payload["chunk_id"]),
    )
    return fused[:max_results]


async def run_knowledge_search(
    deps: AgentDeps,
    *,
    query: str,
    disease_tags: str | None = None,
    max_results: int | None = None,
    knowledge_base_slug: str | None = "mma-pa",
) -> str:
    """Execute knowledge search for unit tests and the tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("knowledge_search")
    if blocked is not None:
        return blocked

    normalized = query.strip()
    if not normalized:
        return json.dumps({"error": "query must not be blank"}, ensure_ascii=False)

    limit = max(1, min(8, max_results if max_results is not None else 5))
    tags = _parse_disease_tags(disease_tags)
    settings = get_settings()
    args: dict[str, object] = {
        "query": normalized[:200],
        "disease_tags": tags,
        "max_results": limit,
        "knowledge_base_slug": knowledge_base_slug,
    }

    started_at = time.monotonic()
    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, args)

    query_embedding: list[float] | None = None
    if settings.knowledge_embedding_enabled and deps.http_client is not None:
        query_embedding = await embed_text(
            normalized,
            deps.http_client,
            settings=settings,
            enabled=True,
        )

    try:
        async with session_factory() as session:
            hits = await search_knowledge_chunks(
                session,
                query=normalized,
                disease_tags=tags,
                max_results=limit,
                knowledge_base_slug=knowledge_base_slug,
                query_embedding=query_embedding,
                current_embedding_model=settings.memory_embedding_model,
            )
    except Exception as exc:
        logger.exception("knowledge_search failed")
        if deps.persist_tool_events and deps.run_id is not None:
            duration_ms = round((time.monotonic() - started_at) * 1000)
            await _persist_tool_result(
                deps.run_id,
                ok=False,
                summary=str(exc)[:500],
                duration_ms=duration_ms,
            )
        return json.dumps({"error": f"knowledge search failed: {exc}"}, ensure_ascii=False)

    response = {
        "query": normalized,
        "disease_tags": tags,
        "count": len(hits),
        "results": hits,
        "embedding_used": query_embedding is not None,
        "note": (
            "Curated educational summaries with citations. "
            "Not a clinical diagnosis; prefer source_url when explaining."
        ),
    }
    if deps.persist_tool_events and deps.run_id is not None:
        embedding_flag = "embedding:on" if query_embedding is not None else "embedding:off"
        summary = f"{len(hits)} hits ({embedding_flag})"
        if hits:
            summary += f": {hits[0]['title']}"
        duration_ms = round((time.monotonic() - started_at) * 1000)
        await _persist_tool_result(
            deps.run_id,
            ok=True,
            summary=summary[:500],
            duration_ms=duration_ms,
        )

    return json.dumps(response, ensure_ascii=False)


async def knowledge_search(
    ctx: RunContext[AgentDeps],
    query: str,
    disease_tags: str | None = None,
    max_results: int = 5,
) -> str:
    """Search curated MMA/PA knowledge chunks by keywords and optional disease tags.

    Prefer this for methylmalonic/propionic acidemia education before generic web_search.
    disease_tags: comma-separated, e.g. isolated_mma,pa,cobalamin_disorder,gene:MMUT
    """

    return await run_knowledge_search(
        ctx.deps,
        query=query,
        disease_tags=disease_tags,
        max_results=max_results,
    )


async def _persist_tool_call(run_id: UUID, args: dict[str, object]) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="knowledge_search",
                args=args,
            )
    except Exception:
        logger.exception("Unable to persist knowledge_search tool_call run=%s", run_id)


async def _persist_tool_result(
    run_id: UUID,
    *,
    ok: bool,
    summary: str,
    duration_ms: int | None = None,
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="knowledge_search",
                provider="postgres",
                ok=ok,
                summary=summary,
                duration_ms=duration_ms,
            )
    except Exception:
        logger.exception("Unable to persist knowledge_search tool_result run=%s", run_id)
