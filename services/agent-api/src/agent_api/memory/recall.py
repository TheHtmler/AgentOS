"""Hybrid recall: always-on profile + keyword/embedding notes."""

from __future__ import annotations

import re
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import MEMORY_HEADER
from agent_api.config import get_settings
from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import UserMemory
from agent_api.memory.embed import cosine_similarity, embed_text

SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(("身高", "身长", "生长", "发育")),
    frozenset(("体重", "公斤", "千克", "kg")),
    frozenset(("报告", "体检", "化验")),
)
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_VECTOR_WEIGHT = 10.0
_MIN_VECTOR_KEEP = 0.28


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0).lower()
        if "\u4e00" <= token[0] <= "\u9fff":
            tokens.update(token[index : index + 2] for index in range(len(token) - 1))
            if len(token) == 1:
                tokens.add(token)
        else:
            tokens.add(token)
    return tokens


def _expanded_tokens(message: str) -> set[str]:
    tokens = _tokens(message)
    expanded = set(tokens)
    lowered_message = message.lower()
    for group in SYNONYM_GROUPS:
        if any(term in lowered_message or term in tokens for term in group):
            expanded.update(group)
    return expanded


def _keyword_score(message: str, memory: UserMemory) -> float:
    expanded = _expanded_tokens(message)
    lowered_message = message.lower()
    tag_hits = sum(
        tag.lower() in expanded
        or (len(tag) >= 2 and tag.lower() in lowered_message)
        for tag in memory.tags
    )
    overlap = sum(token in memory.content.lower() for token in expanded)
    return float(tag_hits * 3 + overlap)


def score_memories(message: str, memories: list[UserMemory]) -> list[UserMemory]:
    """Keyword-only ranking (notes); kept for unit tests."""

    scored = [
        (memory, _keyword_score(message, memory))
        for memory in memories
        if _keyword_score(message, memory) > 0
    ]
    scored.sort(key=lambda item: (item[1], item[0].updated_at), reverse=True)
    return [memory for memory, _ in scored]


def format_memory_block(
    memories: list[UserMemory],
    *,
    exclude_keys: set[str] | None = None,
) -> str | None:
    """Render profile then notes as a compact instruction block.

    ``exclude_keys`` drops profile slots already covered by Case facts so the
    model does not see conflicting height/weight values.
    """

    if not memories:
        return None
    skip = exclude_keys or set()
    profiles = [
        memory
        for memory in memories
        if memory.kind == "profile" and (memory.key is None or memory.key not in skip)
    ]
    notes = [memory for memory in memories if memory.kind != "profile"]
    lines = [MEMORY_HEADER]
    if profiles:
        lines.append("### Profile (always use when relevant)")
        for memory in profiles:
            key = memory.key or "slot"
            lines.append(f"- [{key}] {memory.content}")
    if notes:
        lines.append("### Notes")
        for memory in notes:
            tag_label = ", ".join(memory.tags) if memory.tags else "note"
            lines.append(f"- [{tag_label}] {memory.content}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def _trim_by_chars(memories: list[UserMemory], *, max_chars: int) -> list[UserMemory]:
    selected: list[UserMemory] = []
    used_chars = 0
    for memory in memories:
        rendered_length = len(memory.content) + sum(len(tag) for tag in memory.tags) + 8
        if selected and used_chars + rendered_length > max_chars:
            break
        if not selected and rendered_length > max_chars:
            continue
        selected.append(memory)
        used_chars += rendered_length
    return selected


def select_memories_for_message(
    message: str,
    memories: list[UserMemory],
    *,
    top_k: int = 8,
    max_chars: int = 2_000,
    query_embedding: list[float] | None = None,
) -> list[UserMemory]:
    """Always keep profile slots; hybrid-rank notes by keyword + embedding."""

    profiles = sorted(
        [memory for memory in memories if memory.kind == "profile"],
        key=lambda memory: memory.key or "",
    )
    notes = [memory for memory in memories if memory.kind != "profile"]

    scored_notes: list[tuple[float, UserMemory]] = []
    for memory in notes:
        keyword = _keyword_score(message, memory)
        vector = 0.0
        if query_embedding and memory.embedding:
            vector = cosine_similarity(query_embedding, list(memory.embedding))
        combined = keyword + vector * _VECTOR_WEIGHT
        if keyword > 0 or vector >= _MIN_VECTOR_KEEP:
            scored_notes.append((combined, memory))
    scored_notes.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    ranked_notes = [memory for _, memory in scored_notes[:top_k]]

    # Profile first (always-on), then ranked notes, then size bound.
    ordered = [*profiles, *ranked_notes]
    return _trim_by_chars(ordered, max_chars=max_chars)


async def load_relevant_memories(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    message: str,
    top_k: int = 8,
    max_chars: int = 2_000,
    http_client: httpx.AsyncClient | None = None,
) -> list[UserMemory]:
    """Load profile + hybrid-ranked notes for one user/Agent conversation."""

    memories = await list_active_memories(session, user_id=user_id, agent_id=agent_id)
    query_embedding: list[float] | None = None
    settings = get_settings()
    if (
        http_client is not None
        and settings.memory_embedding_enabled
        and any(memory.kind != "profile" and memory.embedding for memory in memories)
    ):
        query_embedding = await embed_text(message, http_client, settings=settings)

    return select_memories_for_message(
        message,
        memories,
        top_k=top_k,
        max_chars=max_chars,
        query_embedding=query_embedding,
    )
