"""Asynchronous stable-fact extraction for completed Agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import UserMemory
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)
_inflight: set[UUID] = set()
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
# Deterministic fallback when the local model skips anthropometric facts.
_HEIGHT_RE = re.compile(
    r"(?:身高|身长)\s*[：:是为]?\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)?",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"(?:体重)\s*[：:是为]?\s*(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)?",
    re.IGNORECASE,
)

ExtractFactsFn = Callable[[str, str, httpx.AsyncClient], Awaitable[list[dict[str, object]]]]


def _normalize_content(content: str) -> str:
    return _WHITESPACE_RE.sub(" ", content).strip().casefold()


def extract_measurement_facts(user_message: str) -> list[dict[str, object]]:
    """Pull explicit height/weight statements without depending on the LLM."""

    facts: list[dict[str, object]] = []
    height = _HEIGHT_RE.search(user_message)
    if height is not None:
        facts.append(
            {
                "content": f"身高 {height.group(1)}cm",
                "tags": ["身高"],
            },
        )
    weight = _WEIGHT_RE.search(user_message)
    if weight is not None:
        facts.append(
            {
                "content": f"体重 {weight.group(1)}kg",
                "tags": ["体重"],
            },
        )
    return facts


def merge_fact_lists(
    *groups: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Keep the first fact per primary tag across deterministic + model extracts."""

    merged: list[dict[str, object]] = []
    seen_tags: set[str] = set()
    for group in groups:
        for fact in _valid_facts(group):
            tags = cast(list[str], fact["tags"])
            primary = tags[0]
            if primary in seen_tags:
                continue
            seen_tags.add(primary)
            merged.append(fact)
    return merged


def _valid_facts(facts: object) -> list[dict[str, object]]:
    """Keep only well-formed extractor facts that can be persisted safely."""

    if not isinstance(facts, list):
        return []
    valid: list[dict[str, object]] = []
    raw_facts = cast(list[object], facts)
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, dict):
            continue
        fact = cast(dict[str, object], raw_fact)
        content = fact.get("content")
        tags = fact.get("tags")
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(tags, list)
            or not tags
            or not all(
                isinstance(tag, str) and tag.strip() for tag in cast(list[object], tags)
            )
        ):
            continue
        valid.append(
            {
                "content": content.strip(),
                "tags": [tag.strip() for tag in cast(list[str], tags)],
            },
        )
    return valid


async def extract_facts_via_ollama(
    user_message: str,
    assistant_content: str,
    http_client: httpx.AsyncClient,
) -> list[dict[str, object]]:
    """Ask Ollama for stable user facts, returning an empty list on bad output."""

    settings = get_settings()
    prompt = (
        "Extract only stable user facts that may help future conversations. "
        "Always capture durable anthropometrics when present "
        '(height/length/体重 with numbers), using tags like ["身高"] or ["体重"]. '
        "Do not extract temporary requests, assistant opinions, or sensitive guesses. "
        'Reply with JSON only: [{"content":"fact","tags":["topic"],"op":"upsert"}]. '
        "Use an empty JSON array when there are no stable facts.\n\n"
        f"User:\n{user_message[:2_000]}\n\nAssistant:\n{assistant_content[:2_000]}"
    )
    response = await http_client.post(
        settings.ollama_base_url.rstrip("/") + "/chat/completions",
        json={
            "model": settings.ollama_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You extract durable user facts. Output valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 512,
            "temperature": 0,
        },
        timeout=settings.memory_extract_timeout_seconds,
    )
    response.raise_for_status()
    try:
        raw = response.json()["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            return []
        return _valid_facts(json.loads(_CODE_FENCE_RE.sub("", raw.strip())))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("memory extraction returned invalid JSON")
        return []


async def upsert_extracted_facts(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    facts: list[dict[str, object]],
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
) -> int:
    """Archive stale primary-tag facts and insert changed stable facts."""

    created = 0
    for fact in _valid_facts(facts):
        content = fact["content"]
        tags = fact["tags"]
        assert isinstance(content, str)
        assert isinstance(tags, list)
        primary_tag = cast(list[str], tags)[0]
        active = await list_active_memories(session, user_id=user_id, agent_id=agent_id)
        matching = [
            memory
            for memory in active
            if memory.tags and memory.tags[0] == primary_tag
        ]
        normalized = _normalize_content(content)
        duplicate = next(
            (
                memory
                for memory in matching
                if _normalize_content(memory.content) == normalized
            ),
            None,
        )
        if duplicate is not None:
            duplicate.updated_at = datetime.now(UTC)
            continue
        if matching:
            await session.execute(
                update(UserMemory)
                .where(UserMemory.id.in_([memory.id for memory in matching]))
                .values(status="archived"),
            )
        session.add(
            UserMemory(
                user_id=user_id,
                agent_id=agent_id,
                source_thread_id=source_thread_id,
                source_run_id=source_run_id,
                content=content,
                tags=cast(list[str], tags),
                status="active",
            ),
        )
        created += 1
    return created


def schedule_memory_extract(
    *,
    user_id: UUID,
    agent_id: UUID,
    thread_id: UUID,
    run_id: UUID,
    user_message: str,
    assistant_content: str,
    model_semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
    memory_enabled: bool,
    extract_facts: ExtractFactsFn | None = None,
) -> None:
    """Schedule best-effort fact extraction without delaying a completed run."""

    settings = get_settings()
    if not memory_enabled or not settings.memory_extract_enabled or run_id in _inflight:
        return
    if not user_message.strip() or not assistant_content.strip():
        return
    _inflight.add(run_id)
    extractor = extract_facts or extract_facts_via_ollama

    async def _run() -> None:
        try:
            try:
                await asyncio.wait_for(
                    model_semaphore.acquire(),
                    timeout=settings.memory_extract_timeout_seconds,
                )
            except TimeoutError:
                logger.info("memory extraction skipped; model semaphore busy run=%s", run_id)
                return
            try:
                model_facts = await extractor(user_message, assistant_content, http_client)
            finally:
                model_semaphore.release()
            # Regex catches height/weight even when the local model returns [].
            facts = merge_fact_lists(extract_measurement_facts(user_message), model_facts)
            async with session_factory() as session, session.begin():
                count = await upsert_extracted_facts(
                    session,
                    user_id=user_id,
                    agent_id=agent_id,
                    facts=facts,
                    source_thread_id=thread_id,
                    source_run_id=run_id,
                )
            logger.info("memory extraction stored=%s run=%s", count, run_id)
        except Exception:
            logger.exception("memory extraction failed run=%s", run_id)
        finally:
            _inflight.discard(run_id)

    asyncio.create_task(_run(), name=f"memory-extract-{run_id}")
