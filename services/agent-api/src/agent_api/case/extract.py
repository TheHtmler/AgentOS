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
from agent_api.db.case_store import user_can_write_case
from agent_api.db.models import CaseFact
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)
_inflight: set[UUID] = set()
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

# Capture height/weight numbers from colloquial Chinese user text.
_HEIGHT_RE = re.compile(
    r"(?:身高|身长)\s*[是为:]?\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)?",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"(?:体重|重量)\s*[是为:]?\s*(\d+(?:\.\d+)?)\s*(?:kg|公斤)?",
    re.IGNORECASE,
)
_HEIGHT_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:cm|厘米)", re.IGNORECASE)
_WEIGHT_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|公斤)", re.IGNORECASE)

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
    updates: list[CaseFactUpdate] = field(default_factory=list[CaseFactUpdate])


def _normalize_content(content: str) -> str:
    return _WHITESPACE_RE.sub(" ", content).strip().casefold()


def infer_case_fact_key(content: str, tags: list[str]) -> str | None:
    """Infer a stable Case key from content/tags when the model omitted key."""

    haystack = " ".join([content, *tags]).casefold()
    if any(token in haystack for token in ("身高", "身长", "height", "cm", "厘米")):
        if "体重" not in haystack and "weight" not in haystack and "kg" not in haystack:
            return "height_cm"
        if "身高" in haystack or "身长" in haystack or "height" in haystack:
            return "height_cm"
    if any(token in haystack for token in ("体重", "重量", "weight", "kg", "公斤")):
        return "weight_kg"
    if any(token in haystack for token in ("性别", "男", "女", "sex", "male", "female")):
        return "sex"
    if any(token in haystack for token in ("生日", "出生", "date_of_birth", "dob")):
        return "date_of_birth"
    if any(token in haystack for token in ("月龄", "个月", "age_months")):
        return "age_months"
    return None


def slot_hints_from_user_message(user_message: str) -> list[CaseFactUpdate]:
    """Deterministic height/weight hints from the user turn (LLM miss safety net)."""

    text = user_message.strip()
    if not text:
        return []

    hints: list[CaseFactUpdate] = []
    height_match = _HEIGHT_RE.search(text)
    # Fallback: bare "82 cm" only when 身高/身长 also appears nearby in the turn.
    if height_match is None and re.search(r"身高|身长", text):
        height_match = _HEIGHT_UNIT_RE.search(text)
    if height_match is not None:
        value = height_match.group(1)
        hints.append(
            CaseFactUpdate(
                key="height_cm",
                content=f"身高 {value} cm",
                tags=["身高"],
            ),
        )

    weight_match = _WEIGHT_RE.search(text)
    if weight_match is None and re.search(r"体重|重量", text):
        weight_match = _WEIGHT_UNIT_RE.search(text)
    if weight_match is not None:
        value = weight_match.group(1)
        hints.append(
            CaseFactUpdate(
                key="weight_kg",
                content=f"体重 {value} kg",
                tags=["体重"],
            ),
        )
    return hints


def merge_user_slot_hints(
    user_message: str,
    payload: ExtractedCasePayload,
) -> ExtractedCasePayload:
    """Fill missing keyed updates the model omitted but the user clearly stated."""

    hints = slot_hints_from_user_message(user_message)
    if not hints:
        return payload

    present_keys = {item.key for item in payload.updates if item.key}
    merged = list(payload.updates)
    for hint in hints:
        if hint.key and hint.key not in present_keys:
            merged.append(hint)
            present_keys.add(hint.key)

    # Prefer self when the user stated anthropometrics about 宝宝 / 我家孩子.
    attribution = payload.attribution
    if (
        attribution == "unknown"
        and merged
        and re.search(
            r"宝宝|我家|我儿|我女|我的孩子|小孩",
            user_message,
        )
    ):
        attribution = "self"

    if len(merged) != len(payload.updates) or attribution != payload.attribution:
        logger.info(
            "case extract merged user slot hints: before=%s after=%s attribution=%s→%s",
            len(payload.updates),
            len(merged),
            payload.attribution,
            attribution,
        )
    return ExtractedCasePayload(attribution=attribution, updates=merged)


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
    keyless = 0
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
            content = content.strip()
            if key is None:
                key = infer_case_fact_key(content, tags)
                if key is None:
                    keyless += 1
            updates.append(CaseFactUpdate(key=key, content=content, tags=tags))
    if keyless:
        logger.warning("case extract had %s keyless updates after inference", keyless)
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
        '  "updates": [\n'
        '    {"key":"height_cm","content":"身高 82.5 cm","tags":["身高"]},\n'
        '    {"key":"weight_kg","content":"体重 15.2 kg","tags":["体重"]}\n'
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Prefer stable keys when applicable: height_cm, weight_kg, sex, "
        "date_of_birth, age_months (one update object per key).\n"
        "- If the user states BOTH height and weight in this turn, you MUST emit "
        "two updates (height_cm and weight_kg).\n"
        "- Only include keys the user (or clearly confirmed assistant recap) stated "
        "in THIS turn. If they only update height and do not mention weight, omit "
        "weight — never clear or rewrite unmentioned slots.\n"
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
                        "You extract Case archive facts with attribution. Output valid JSON only."
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
            and _normalize_content(existing.content) == _normalize_content(fact_update.content)
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
    user_id: UUID,
    case_id: UUID,
    payload: ExtractedCasePayload,
    source_thread_id: UUID | None,
    source_run_id: UUID | None,
) -> int:
    """Apply attribution policy and persist updates. Returns rows written."""

    if not await user_can_write_case(session, user_id=user_id, case_id=case_id):
        return 0
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
    user_id: UUID,
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
    user_text = user_message.strip()
    assistant_text = assistant_content.strip()
    # Prefer user+assistant; still run when the user stated slot values even if
    # the assistant reply is empty (e.g. cancelled mid-stream after user write).
    if not user_text:
        return
    if not assistant_text and not slot_hints_from_user_message(user_text):
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
                if assistant_text:
                    payload = await extractor(user_text, assistant_text, http_client)
                else:
                    # Deterministic path only — no model call without assistant context.
                    payload = ExtractedCasePayload(attribution="unknown", updates=[])
                payload = merge_user_slot_hints(user_text, payload)
                if not payload.updates and slot_hints_from_user_message(user_text):
                    logger.warning(
                        "case extraction empty after merge despite user slot hints run=%s",
                        run_id,
                    )
            finally:
                model_semaphore.release()
            async with session_factory() as session, session.begin():
                count = await apply_case_extract(
                    session,
                    user_id=user_id,
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
