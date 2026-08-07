"""Format confirmed Case facts for prompt injection."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.case_store import (
    list_confirmed_facts,
    list_keyed_fact_history,
    list_proposed_facts,
)
from agent_api.db.models import CaseFact

CASE_HEADER = "## Case profile (confirmed)"

_HISTORY_KEYS = ("height_cm", "weight_kg", "sex", "date_of_birth", "age_months")


def format_recorded_at(
    value: datetime | None,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    """Render a fact timestamp in the runtime timezone for the model."""

    if value is None:
        return "unknown"
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
        timezone_name = "UTC"
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=ZoneInfo("UTC"))
    local = stamp.astimezone(tz)
    return f"{local.strftime('%Y-%m-%d %H:%M')} {timezone_name}"


def current_facts_by_key(facts: list[CaseFact]) -> list[CaseFact]:
    """Keep the newest confirmed row per key; keyless facts stay as-is (newest first)."""

    seen_keys: set[str] = set()
    selected: list[CaseFact] = []
    for fact in facts:
        if fact.key:
            if fact.key in seen_keys:
                continue
            seen_keys.add(fact.key)
        selected.append(fact)
    return selected


def history_excluding_current(
    history: list[CaseFact],
    current: list[CaseFact],
) -> list[CaseFact]:
    """Drop rows already shown under Current so History is prior values only."""

    current_ids = {fact.id for fact in current}
    return [fact for fact in history if fact.id not in current_ids]


def format_case_block(
    facts: list[CaseFact],
    *,
    history: list[CaseFact] | None = None,
    proposed: list[CaseFact] | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> str | None:
    """Render current Case facts (+ prior history / proposed) with recorded_at stamps."""

    current = current_facts_by_key(facts)
    prior = history_excluding_current(history or [], current)
    pending = current_facts_by_key(proposed or [])
    if not current and not prior and not pending:
        return None

    lines = [
        CASE_HEADER,
        "Rules for answering from this block:",
        '- For "目前/现在/当前", use ### Current only (one value per key; newest).',
        '- For "什么时候记录/历史": if History section is empty, say 暂无更早记录 once;',
        "  do not repeat Current rows as History.",
        "- ### Proposed rows are not confirmed yet — do not treat them as Current;",
        "  mention 待确认 and prefer case_slot_collect / case_attribution_confirm.",
        "- Prefer Case over overlapping user_memories profile slots for the same key.",
        "- Keep factual replies short unless the user asks for analysis.",
        "- Missing critical Case slots for the task: call case_slot_collect (HITL form),",
        "  do not write a long essay ask.",
        "### Current",
    ]
    if not current:
        lines.append("- (none)")
    for fact in current:
        label = fact.key or (", ".join(fact.tags) if fact.tags else "fact")
        recorded = format_recorded_at(fact.updated_at or fact.created_at, timezone_name=timezone_name)
        lines.append(f"- [{label}] {fact.content} (recorded_at: {recorded})")

    lines.append("### History (prior values only; newest first; empty = no earlier records)")
    if not prior:
        lines.append("- (none)")
    else:
        for fact in prior:
            label = fact.key or "fact"
            recorded = format_recorded_at(
                fact.updated_at or fact.created_at,
                timezone_name=timezone_name,
            )
            state = fact.status
            lines.append(f"- [{label}] {fact.content} @ {recorded} [{state}]")

    if pending:
        lines.append("### Proposed (pending confirmation; not Current)")
        for fact in pending:
            label = fact.key or (", ".join(fact.tags) if fact.tags else "fact")
            recorded = format_recorded_at(
                fact.updated_at or fact.created_at,
                timezone_name=timezone_name,
            )
            lines.append(f"- [{label}] {fact.content} (recorded_at: {recorded})")

    return "\n".join(lines)


async def load_case_block(
    session: AsyncSession,
    *,
    case_id: UUID,
    timezone_name: str = "Asia/Shanghai",
) -> str | None:
    """Load confirmed facts plus keyed history for injection."""

    block, _keys = await load_case_injection(
        session,
        case_id=case_id,
        timezone_name=timezone_name,
    )
    return block


async def load_case_injection(
    session: AsyncSession,
    *,
    case_id: UUID,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[str | None, set[str]]:
    """Load Case prompt block and the confirmed keys for memory dedupe."""

    facts = await list_confirmed_facts(session, case_id=case_id)
    history = await list_keyed_fact_history(
        session,
        case_id=case_id,
        keys=_HISTORY_KEYS,
    )
    proposed = await list_proposed_facts(session, case_id=case_id)
    block = format_case_block(
        facts,
        history=history,
        proposed=proposed,
        timezone_name=timezone_name,
    )
    return block, case_keys_from_facts(facts)


def case_keys_from_facts(facts: list[CaseFact]) -> set[str]:
    """Keys present on confirmed facts (for suppressing duplicate memory slots)."""

    return {fact.key for fact in facts if fact.key}
