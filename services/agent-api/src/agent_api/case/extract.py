"""Asynchronous Case fact extraction with attribution-aware write policy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.models import CaseFact
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)
_inflight: set[UUID] = set()
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

Attribution = Literal["self", "other", "hypothetical", "unknown"]

ExtractCaseFn = Callable[
    [str, str, httpx.AsyncClient],
    Awaitable["ExtractedCasePayload"],
]


@dataclass
class CaseFactUpdate:
    key: str | None
    content: str
    tags: list[str]


@dataclass
class ExtractedCasePayload:
    attribution: Attribution = "unknown"
    updates: list[CaseFactUpdate] = field(default_factory=list)


def _normalize_content(content: str) -> str:
    return _WHITESPACE_RE.sub(" ", content).strip().casefold()


def parse_case_extract_payload(raw: object) -> ExtractedCasePayload:
    """Parse extractor JSON into attribution + case updates."""

    if not isinstance(raw, dict):
        return ExtractedCasePayload()
    payload = cast(dict[str, object], raw)
    attribution_raw = payload.get("attribution", "unknown")
    attribution: Attribution = "unknown"
    if isinstance(attribution_raw, str) and attribution_raw in {
        "self",
        "other",
        "hypothetical",
        "unknown",
    }:
        attribution = cast(Attribution, attribution_raw)

    updates_raw = payload.get("updates")
    updates: list[CaseFactUpdate] = []
    if isinstance(updates_raw, list):
        for item in cast(list[object], updates_raw):
            if not isinstance(item, dict):
                continue
            row = cast(dict[str, object], item)
            content = row.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            key_raw = row.get("key")
            key = key_raw.strip() if isinstance(key_raw, str) and key_raw.strip() else None
            tags_raw = row.get("tags")
            tags: list[str] = []
            if isinstance(tags_raw, list):
                tags = [
                    tag.strip()
                    for tag in cast(list[object], tags_raw)
                    if isinstance(tag, str) and tag.strip()
                ]
            updates.append(CaseFactUpdate(key=key, content=content.strip(), tags=tags))
    return ExtractedCasePayload(attribution=attribution, updates=updates)


async def extract_case_via_ollama(
    user_message: str,
    assistant_content: str,
    http_client: httpx.AsyncClient,
) -> ExtractedCasePayload:
    """Ask Ollama for Case updates + attribution; empty on bad output."""

    settings = get_settings()
    prompt = (
        "Extract durable Case archive updates about the subject of this conversation.\n"
        "Return JSON only:\n"
        "{\n"
        '  "attribution": "self"|"other"|"hypothetical"|"unknown",\n'
        '  "updates": [{"key":"height_cm","content":"身高 82.5 cm","tags":["身高"]}]\n'
        "}\n"
        "Rules:\n"
        "- Prefer stable keys when applicable: height_cm, weight_kg, sex, "
        "date_of_birth, age_months (one update object per key).\n"
        "- Only include keys the user (or clearly confirmed assistant recap) stated "
        "in THIS turn. If they only update height, omit weight — never clear or "
        "rewrite unmentioned slots.\n"
        "- attribution=self for the user's own default subject (e.g. 宝宝 / 我家孩子 / "
        "the Case already in context).\n"
        "- attribution=other when helping someone else's child or a third party.\n"
        "- attribution=hypothetical for examples, what-if, or textbook scenarios.\n"
        "- attribution=unknown only when ownership is truly unclear.\n"
        "- Do not invent values. Empty updates when nothing durable is present.\n\n"
        f"User:\n{user_message[:2_000]}\n\nAssistant:\n{assistant_content[:2_000]}"
    )
    response = await http_client.post(
        settings.ollama_base_url.rstrip("/") + "/chat/completions",
        json={
            "model": settings.ollama_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You extract Case archive facts with attribution. "
                        "Output valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 768,
            "temperature": 0,
        },
        timeout=settings.case_extract_timeout_seconds,
    )
    response.raise_for_status()
    try:
        raw = response.json()["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            return ExtractedCasePayload()
        parsed = json.loads(_CODE_FENCE_RE.sub("", raw.strip()))
        return parse_case_extract_payload(parsed)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("case extraction returned invalid JSON")
        return ExtractedCasePayload()


async def upsert_case_fact(
    session: AsyncSession,
    *,
    case_id: UUID,
    fact_update: CaseFactUpdate,
    status: str,
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
) -> bool:
    """Insert or replace one Case fact; keyed slots replace prior same-key rows."""

    if fact_update.key:
        existing = await session.scalar(
            select(CaseFact)
            .where(
                CaseFact.case_id == case_id,
                CaseFact.key == fact_update.key,
                CaseFact.status.in_(("proposed", "confirmed")),
            )
            .order_by(CaseFact.updated_at.desc())
            .limit(1),
        )
        if (
            existing is not None
            and existing.status == status
            and _normalize_content(existing.content)
            == _normalize_content(fact_update.content)
        ):
            existing.updated_at = datetime.now(UTC)
            return False
        # Archive prior active rows for the same slot before writing the new value.
        await session.execute(
            update(CaseFact)
            .where(
                CaseFact.case_id == case_id,
                CaseFact.key == fact_update.key,
                CaseFact.status.in_(("proposed", "confirmed")),
            )
            .values(status="archived", updated_at=datetime.now(UTC)),
        )

    session.add(
        CaseFact(
            case_id=case_id,
            key=fact_update.key,
            content=fact_update.content,
            tags=fact_update.tags,
            status=status,
            source_thread_id=source_thread_id,
            source_run_id=source_run_id,
        ),
    )
    return True


def apply_attribution_policy(
    payload: ExtractedCasePayload,
) -> Literal["confirm", "propose", "skip"]:
    """Map attribution to write action (pure; used by tests and apply path)."""

    if not payload.updates:
        return "skip"
    if payload.attribution == "self":
        return "confirm"
    if payload.attribution == "unknown":
        # Post-run extract cannot reopen HITL on a completed Run; store proposed
        # for REST / next-turn confirmation. In-run model may still call
        # case_attribution_confirm (requires_approval).
        return "propose"
    # other / hypothetical must never silently overwrite the Case.
    return "skip"


async def apply_case_extract(
    session: AsyncSession,
    *,
    case_id: UUID,
    payload: ExtractedCasePayload,
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
) -> int:
    """Apply attribution policy and persist updates. Returns rows written."""

    action = apply_attribution_policy(payload)
    if action == "skip":
        return 0
    status = "confirmed" if action == "confirm" else "proposed"
    written = 0
    for update_row in payload.updates:
        if await upsert_case_fact(
            session,
            case_id=case_id,
            fact_update=update_row,
            status=status,
            source_thread_id=source_thread_id,
            source_run_id=source_run_id,
        ):
            written += 1
    return written


def schedule_case_extract(
    *,
    case_id: UUID | None,
    case_enabled: bool,
    thread_id: UUID,
    run_id: UUID,
    user_message: str,
    assistant_content: str,
    model_semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
    extract_case: ExtractCaseFn | None = None,
) -> None:
    """Schedule best-effort Case extraction without delaying a completed run."""

    settings = get_settings()
    if (
        not case_enabled
        or case_id is None
        or not settings.case_extract_enabled
        or run_id in _inflight
    ):
        return
    if not user_message.strip() or not assistant_content.strip():
        return
    _inflight.add(run_id)
    extractor = extract_case or extract_case_via_ollama

    async def _run() -> None:
        try:
            try:
                await asyncio.wait_for(
                    model_semaphore.acquire(),
                    timeout=settings.case_extract_timeout_seconds,
                )
            except TimeoutError:
                logger.info("case extraction skipped; model semaphore busy run=%s", run_id)
                return
            try:
                payload = await extractor(user_message, assistant_content, http_client)
            finally:
                model_semaphore.release()
            async with session_factory() as session, session.begin():
                count = await apply_case_extract(
                    session,
                    case_id=case_id,
                    payload=payload,
                    source_thread_id=thread_id,
                    source_run_id=run_id,
                )
            logger.info(
                "case extraction stored=%s attribution=%s updates=%s run=%s",
                count,
                payload.attribution,
                len(payload.updates),
                run_id,
            )
        except Exception:
            logger.exception("case extraction failed run=%s", run_id)
        finally:
            _inflight.discard(run_id)

    asyncio.create_task(_run(), name=f"case-extract-{run_id}")
