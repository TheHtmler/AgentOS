"""Asynchronous structured memory extraction (profile slots + notes)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import UserMemory
from agent_api.db.session import session_factory
from agent_api.memory.embed import embed_text
from agent_api.memory.profile import coerce_profile_dict, normalize_profile_value

logger = logging.getLogger(__name__)
_inflight: set[UUID] = set()
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

ExtractMemoryFn = Callable[
    [str, str, httpx.AsyncClient],
    Awaitable["ExtractedMemoryPayload"],
]


@dataclass
class ExtractedMemoryPayload:
    """Structured extractor output: always-on profile slots plus free-text notes."""

    profile: dict[str, object] = field(default_factory=dict[str, object])
    notes: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


def _normalize_content(content: str) -> str:
    return _WHITESPACE_RE.sub(" ", content).strip().casefold()


def _valid_notes(notes: object) -> list[dict[str, object]]:
    if not isinstance(notes, list):
        return []
    valid: list[dict[str, object]] = []
    for raw_note in cast(list[object], notes):
        if not isinstance(raw_note, dict):
            continue
        note = cast(dict[str, object], raw_note)
        content = note.get("content")
        tags = note.get("tags")
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(tags, list)
            or not tags
            or not all(isinstance(tag, str) and tag.strip() for tag in cast(list[object], tags))
        ):
            continue
        valid.append(
            {
                "content": content.strip(),
                "tags": [tag.strip() for tag in cast(list[str], tags)],
            },
        )
    return valid


def parse_extracted_payload(raw: object) -> ExtractedMemoryPayload:
    """Accept the structured object shape, or a legacy fact-array for tests."""

    if isinstance(raw, list):
        # Legacy: [{"content","tags"}] → treat as notes only.
        return ExtractedMemoryPayload(notes=_valid_notes(cast(object, raw)))
    if not isinstance(raw, dict):
        return ExtractedMemoryPayload()
    payload = cast(dict[str, object], raw)
    return ExtractedMemoryPayload(
        profile=coerce_profile_dict(payload.get("profile")),
        notes=_valid_notes(payload.get("notes")),
    )


async def extract_memory_via_ollama(
    user_message: str,
    assistant_content: str,
    http_client: httpx.AsyncClient,
) -> ExtractedMemoryPayload:
    """Ask Ollama for profile slots + notes; empty payload on bad output."""

    settings = get_settings()
    prompt = (
        "Extract durable user memory for future conversations.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "profile": {\n'
        '    "height_cm": number|null,\n'
        '    "weight_kg": number|null,\n'
        '    "sex": "male"|"female"|null,\n'
        '    "date_of_birth": "YYYY-MM-DD"|null,\n'
        '    "age_months": number|null\n'
        "  },\n"
        '  "notes": [{"content":"fact","tags":["topic"]}]\n'
        "}\n"
        "Rules:\n"
        "- Put anthropometrics and sex/DOB/age into profile (omit unknown keys or use null).\n"
        "- Put other stable facts (allergies, preferences, diagnoses mentioned) into notes.\n"
        "- Do not invent values. Do not extract temporary requests or assistant opinions.\n"
        "- Use empty profile object and empty notes array when nothing durable is present.\n\n"
        f"User:\n{user_message[:2_000]}\n\nAssistant:\n{assistant_content[:2_000]}"
    )
    response = await http_client.post(
        settings.ollama_base_url.rstrip("/") + "/chat/completions",
        json={
            "model": settings.ollama_model,
            "messages": [
                {
                    "role": "system",
                    "content": ("You extract structured user memory. Output valid JSON only."),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 768,
            "temperature": 0,
        },
        timeout=settings.memory_extract_timeout_seconds,
    )
    response.raise_for_status()
    try:
        raw = response.json()["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            return ExtractedMemoryPayload()
        parsed = json.loads(_CODE_FENCE_RE.sub("", raw.strip()))
        return parse_extracted_payload(parsed)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("memory extraction returned invalid JSON")
        return ExtractedMemoryPayload()


async def upsert_profile_slots(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID | None,
    profile: dict[str, object],
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
) -> int:
    """Upsert always-on profile slots keyed by slot name."""

    changed = 0
    for key, value in profile.items():
        rendered = normalize_profile_value(key, value)
        if rendered is None:
            continue
        slot_key, tags, content = rendered
        statement = select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.agent_id == agent_id,
            UserMemory.kind == "profile",
            UserMemory.key == slot_key,
            UserMemory.status == "active",
        )
        if case_id is None:
            statement = statement.where(UserMemory.case_id.is_(None))
        else:
            statement = statement.where(UserMemory.case_id == case_id)
        existing = await session.scalar(statement)
        if existing is not None:
            if _normalize_content(existing.content) == _normalize_content(content):
                existing.updated_at = datetime.now(UTC)
                continue
            existing.content = content
            existing.tags = tags
            existing.source_thread_id = source_thread_id
            existing.source_run_id = source_run_id
            existing.updated_at = datetime.now(UTC)
            changed += 1
            continue
        session.add(
            UserMemory(
                user_id=user_id,
                agent_id=agent_id,
                case_id=case_id,
                source_thread_id=source_thread_id,
                source_run_id=source_run_id,
                kind="profile",
                key=slot_key,
                content=content,
                tags=tags,
                status="active",
            ),
        )
        changed += 1
    return changed


async def upsert_note_facts(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID | None,
    notes: list[dict[str, object]],
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
    embeddings: dict[str, list[float] | None] | None = None,
) -> int:
    """Archive stale primary-tag notes and insert changed note facts."""

    created = 0
    embedding_map = embeddings or {}
    for note in _valid_notes(notes):
        content = cast(str, note["content"])
        tags = cast(list[str], note["tags"])
        primary_tag = tags[0]
        active = await list_active_memories(
            session,
            user_id=user_id,
            agent_id=agent_id,
            case_id=case_id,
        )
        matching = [
            memory
            for memory in active
            if memory.kind == "note" and memory.tags and memory.tags[0] == primary_tag
        ]
        normalized = _normalize_content(content)
        duplicate = next(
            (memory for memory in matching if _normalize_content(memory.content) == normalized),
            None,
        )
        if duplicate is not None:
            duplicate.updated_at = datetime.now(UTC)
            if duplicate.embedding is None and embedding_map.get(content) is not None:
                duplicate.embedding = embedding_map[content]
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
                case_id=case_id,
                source_thread_id=source_thread_id,
                source_run_id=source_run_id,
                kind="note",
                key=None,
                content=content,
                tags=tags,
                embedding=embedding_map.get(content),
                status="active",
            ),
        )
        created += 1
    return created


async def upsert_extracted_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    payload: ExtractedMemoryPayload,
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
    case_id: UUID | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    """Persist profile slots and notes; embed notes when enabled."""

    profile_count = await upsert_profile_slots(
        session,
        user_id=user_id,
        agent_id=agent_id,
        case_id=case_id,
        profile=payload.profile,
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
    )
    embeddings: dict[str, list[float] | None] = {}
    if http_client is not None:
        for note in _valid_notes(payload.notes):
            content = cast(str, note["content"])
            embeddings[content] = await embed_text(content, http_client)
    note_count = await upsert_note_facts(
        session,
        user_id=user_id,
        agent_id=agent_id,
        case_id=case_id,
        notes=payload.notes,
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
        embeddings=embeddings,
    )
    return profile_count + note_count


# Compatibility shim used by older tests that still pass fact lists.
async def upsert_extracted_facts(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    facts: list[dict[str, object]],
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
    case_id: UUID | None = None,
) -> int:
    """Upsert a legacy fact list as notes (or profile when tags map to slots)."""

    profile: dict[str, object] = {}
    notes: list[dict[str, object]] = []
    for fact in facts:
        tags = fact.get("tags")
        content = fact.get("content")
        if not isinstance(tags, list) or not tags or not isinstance(content, str):
            continue
        primary = str(cast(list[object], tags)[0])
        if primary == "身高":
            # Best-effort parse number from legacy content for tests.
            digits = re.search(r"(\d+(?:\.\d+)?)", content)
            if digits:
                profile["height_cm"] = float(digits.group(1))
                continue
        if primary == "体重":
            digits = re.search(r"(\d+(?:\.\d+)?)", content)
            if digits:
                profile["weight_kg"] = float(digits.group(1))
                continue
        notes.append({"content": content, "tags": [str(tag) for tag in cast(list[object], tags)]})
    return await upsert_extracted_memory(
        session,
        user_id=user_id,
        agent_id=agent_id,
        case_id=case_id,
        payload=ExtractedMemoryPayload(profile=profile, notes=notes),
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
        http_client=None,
    )


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
    case_id: UUID | None = None,
    extract_memory: ExtractMemoryFn | None = None,
    extract_facts: ExtractMemoryFn | None = None,
) -> None:
    """Schedule best-effort structured extraction without delaying a completed run."""

    settings = get_settings()
    if not memory_enabled or not settings.memory_extract_enabled or run_id in _inflight:
        return
    if not user_message.strip() or not assistant_content.strip():
        return
    _inflight.add(run_id)
    extractor = extract_memory or extract_facts or extract_memory_via_ollama

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
                payload = await extractor(user_message, assistant_content, http_client)
            finally:
                model_semaphore.release()
            if isinstance(payload, list):
                payload = parse_extracted_payload(payload)
            async with session_factory() as session, session.begin():
                count = await upsert_extracted_memory(
                    session,
                    user_id=user_id,
                    agent_id=agent_id,
                    case_id=case_id,
                    payload=payload,
                    source_thread_id=thread_id,
                    source_run_id=run_id,
                    http_client=http_client,
                )
            logger.info(
                "memory extraction stored=%s profile_keys=%s notes=%s run=%s",
                count,
                sorted(payload.profile.keys()),
                len(payload.notes),
                run_id,
            )
        except Exception:
            logger.exception("memory extraction failed run=%s", run_id)
        finally:
            _inflight.discard(run_id)

    asyncio.create_task(_run(), name=f"memory-extract-{run_id}")
