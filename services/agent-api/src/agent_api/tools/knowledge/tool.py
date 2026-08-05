"""Built-in keyword knowledge search over curated disease chunks."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, cast
from uuid import UUID

from pydantic_ai import RunContext
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agent_api.db.session import session_factory
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize_query(query: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) >= 2][:12]


def _parse_disease_tags(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()][:16]


def _score_chunk(content: str, title: str, tags: list[str], tokens: list[str]) -> int:
    haystack = f"{title}\n{content}".lower()
    tag_blob = " ".join(tags).lower()
    score = 0
    for token in tokens:
        if token in haystack:
            score += 3
        if token in tag_blob:
            score += 2
    return score


async def search_knowledge_chunks(
    session: AsyncSession,
    *,
    query: str,
    disease_tags: list[str],
    max_results: int,
    knowledge_base_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Keyword + optional tag filter over published knowledge chunks."""

    tokens = _tokenize_query(query)
    if not tokens and not disease_tags:
        return []

    stmt: Select[tuple[KnowledgeChunk, KnowledgeDocument, KnowledgeBase]] = (
        select(KnowledgeChunk, KnowledgeDocument, KnowledgeBase)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .join(KnowledgeBase, KnowledgeDocument.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBase.status == "active")
    )
    if knowledge_base_slug:
        stmt = stmt.where(KnowledgeBase.slug == knowledge_base_slug)
    if disease_tags:
        stmt = stmt.where(KnowledgeChunk.tags.overlap(disease_tags))

    if tokens:
        like_clauses: list[ColumnElement[bool]] = []
        for token in tokens:
            pattern = f"%{token}%"
            like_clauses.append(KnowledgeChunk.content.ilike(pattern))
            like_clauses.append(KnowledgeChunk.title.ilike(pattern))
            like_clauses.append(
                func.array_to_string(KnowledgeChunk.tags, " ").ilike(pattern),
            )
        stmt = stmt.where(or_(*like_clauses))

    # Pull a modest candidate set, then rank in Python for predictable MVP scoring.
    stmt = stmt.order_by(KnowledgeDocument.slug, KnowledgeChunk.chunk_index).limit(80)
    rows = (await session.execute(stmt)).all()

    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk, document, base in rows:
        tags = [str(item) for item in cast(list[Any], chunk.tags or [])]
        score = _score_chunk(chunk.content, chunk.title, tags, tokens)
        if disease_tags:
            overlap = len(set(disease_tags) & {tag.lower() for tag in tags})
            score += overlap * 4
        if score <= 0 and tokens:
            continue
        scored.append(
            (
                score,
                {
                    "chunk_id": str(chunk.id),
                    "title": chunk.title,
                    "content": chunk.content,
                    "tags": tags,
                    "document_slug": document.slug,
                    "document_title": document.title,
                    "source_url": document.source_url,
                    "source_label": document.source_label,
                    "version_label": document.version_label,
                    "knowledge_base": base.slug,
                    "score": score,
                },
            ),
        )

    scored.sort(key=lambda item: (-item[0], item[1]["document_slug"], item[1]["title"]))
    return [payload for _, payload in scored[:max_results]]


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
    args: dict[str, object] = {
        "query": normalized[:200],
        "disease_tags": tags,
        "max_results": limit,
        "knowledge_base_slug": knowledge_base_slug,
    }

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, args)

    try:
        async with session_factory() as session:
            hits = await search_knowledge_chunks(
                session,
                query=normalized,
                disease_tags=tags,
                max_results=limit,
                knowledge_base_slug=knowledge_base_slug,
            )
    except Exception as exc:
        logger.exception("knowledge_search failed")
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(deps.run_id, ok=False, summary=str(exc)[:500])
        return json.dumps({"error": f"knowledge search failed: {exc}"}, ensure_ascii=False)

    response = {
        "query": normalized,
        "disease_tags": tags,
        "count": len(hits),
        "results": hits,
        "note": (
            "Curated educational summaries with citations. "
            "Not a clinical diagnosis; prefer source_url when explaining."
        ),
    }
    if deps.persist_tool_events and deps.run_id is not None:
        summary = f"{len(hits)} hits" + (f": {hits[0]['title']}" if hits else "")
        await _persist_tool_result(deps.run_id, ok=True, summary=summary[:500])

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


async def _persist_tool_result(run_id: UUID, *, ok: bool, summary: str) -> None:
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
            )
    except Exception:
        logger.exception("Unable to persist knowledge_search tool_result run=%s", run_id)
