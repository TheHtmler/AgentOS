"""Keyword recall for Agent-scoped user facts, with recent-fact fallback."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import MEMORY_HEADER
from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import UserMemory

SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    # Growth assessment wording often says 生长/发育 without repeating 身高.
    frozenset(("身高", "身长", "生长", "发育")),
    frozenset(("体重", "公斤", "千克", "kg")),
    frozenset(("报告", "体检", "化验")),
)
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")


def _tokens(text: str) -> set[str]:
    """Return alphanumeric words plus Chinese bigrams for meaningful overlap."""

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
    """Include synonym groups when a group member appears in the message."""

    tokens = _tokens(message)
    expanded = set(tokens)
    lowered_message = message.lower()
    for group in SYNONYM_GROUPS:
        if any(term in lowered_message or term in tokens for term in group):
            expanded.update(group)
    return expanded


def _score_memory(message: str, memory: UserMemory) -> tuple[int, int]:
    expanded = _expanded_tokens(message)
    lowered_message = message.lower()
    tag_hits = sum(
        tag.lower() in expanded
        or (len(tag) >= 2 and tag.lower() in lowered_message)
        for tag in memory.tags
    )
    overlap = sum(token in memory.content.lower() for token in expanded)
    return tag_hits, overlap


def score_memories(message: str, memories: list[UserMemory]) -> list[UserMemory]:
    """Rank matching memories by tag, content overlap, then freshness."""

    scored = [
        (memory, *_score_memory(message, memory))
        for memory in memories
        if any(_score_memory(message, memory))
    ]
    scored.sort(key=lambda item: (item[1], item[2], item[0].updated_at), reverse=True)
    return [memory for memory, _, _ in scored]


def _recent_memories(memories: list[UserMemory]) -> list[UserMemory]:
    """Freshest-first ordering used when the new message has no keyword hits."""

    return sorted(memories, key=lambda memory: memory.updated_at, reverse=True)


def format_memory_block(memories: list[UserMemory]) -> str | None:
    """Render recalled facts as a compact instruction block."""

    if not memories:
        return None
    facts = "\n".join(f"- [{', '.join(memory.tags)}] {memory.content}" for memory in memories)
    return f"{MEMORY_HEADER}\n{facts}"


def select_memories_for_message(
    message: str,
    memories: list[UserMemory],
    *,
    top_k: int = 8,
    max_chars: int = 2_000,
) -> list[UserMemory]:
    """Prefer keyword hits; if none, still inject recent facts (new-thread soft profile)."""

    ranked = score_memories(message, memories)
    if not ranked:
        ranked = _recent_memories(memories)

    selected: list[UserMemory] = []
    used_chars = 0
    for memory in ranked[:top_k]:
        rendered_length = len(memory.content) + sum(len(tag) for tag in memory.tags) + 8
        if selected and used_chars + rendered_length > max_chars:
            break
        if not selected and rendered_length > max_chars:
            continue
        selected.append(memory)
        used_chars += rendered_length
    return selected


async def load_relevant_memories(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    message: str,
    top_k: int = 8,
    max_chars: int = 2_000,
) -> list[UserMemory]:
    """Load and size-bound ranked facts for one user/Agent conversation."""

    return select_memories_for_message(
        message,
        await list_active_memories(session, user_id=user_id, agent_id=agent_id),
        top_k=top_k,
        max_chars=max_chars,
    )
