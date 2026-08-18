"""Deferred HITL tool: collect missing Case slot values from the user."""

from __future__ import annotations

import json
from typing import cast

from pydantic_ai import RunContext

from agent_api.case.extract import CaseFactUpdate, upsert_case_fact
from agent_api.db.case_store import user_can_write_case
from agent_api.db.session import session_factory
from agent_api.tools.search.tool import AgentDeps

_LABEL_HINTS = {
    "height_cm": ("身高", "身高"),
    "weight_kg": ("体重", "体重"),
    "sex": ("性别", "性别"),
    "date_of_birth": ("出生日期", "生日"),
    "age_months": ("月龄", "月龄"),
    "diagnosis_subtype": ("诊断分型/基因", "诊断分型/基因"),
}


def _parse_fields(fields_json: str) -> list[dict[str, str]]:
    try:
        raw = json.loads(fields_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    fields: list[dict[str, str]] = []
    for raw_item in cast(list[object], raw):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        key = key.strip()
        label_raw = item.get("label")
        label = (
            label_raw.strip()
            if isinstance(label_raw, str) and label_raw.strip()
            else _LABEL_HINTS.get(key, (key, key))[0]
        )
        unit_raw = item.get("unit")
        unit = unit_raw.strip() if isinstance(unit_raw, str) and unit_raw.strip() else ""
        reason_raw = item.get("reason")
        reason = reason_raw.strip() if isinstance(reason_raw, str) and reason_raw.strip() else ""
        row = {"key": key, "label": label}
        if unit:
            row["unit"] = unit
        if reason:
            row["reason"] = reason
        fields.append(row)
    return fields


def _content_for(key: str, value: str, label: str, unit: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if key in _LABEL_HINTS and not any(ch.isalpha() for ch in text if ord(ch) < 128):
        # Numeric-ish value: prefix with Chinese label + unit for readable Case content.
        suffix = f" {unit}" if unit and unit not in text else ""
        return f"{label} {text}{suffix}".strip()
    return text


async def run_case_slot_collect(
    deps: AgentDeps,
    *,
    fields_json: str,
    values: dict[str, object] | None = None,
) -> str:
    """Persist user-provided Case slot values as confirmed facts."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("case_slot_collect")
    if blocked is not None:
        return blocked

    if deps.case_id is None or deps.user_id is None:
        return json.dumps(
            {"error": "No Case is bound to this conversation"},
            ensure_ascii=False,
        )

    fields = _parse_fields(fields_json)
    if not fields:
        return json.dumps(
            {"error": "fields_json must be a non-empty JSON array"}, ensure_ascii=False
        )

    provided = values if isinstance(values, dict) else {}
    written_keys: list[str] = []
    missing_keys: list[str] = []

    async with session_factory() as session, session.begin():
        if not await user_can_write_case(
            session,
            user_id=deps.user_id,
            case_id=deps.case_id,
        ):
            return json.dumps(
                {"error": "This Case is read-only for the current user"},
                ensure_ascii=False,
            )
        for field in fields:
            key = field["key"]
            raw_value = provided.get(key)
            if not isinstance(raw_value, str) or not raw_value.strip():
                missing_keys.append(key)
                continue
            content = _content_for(
                key,
                raw_value,
                field["label"],
                field.get("unit", ""),
            )
            if not content:
                missing_keys.append(key)
                continue
            tags = [field["label"]]
            await upsert_case_fact(
                session,
                case_id=deps.case_id,
                fact_update=CaseFactUpdate(key=key, content=content, tags=tags),
                status="confirmed",
                source_thread_id=None,
                source_run_id=deps.run_id,
            )
            written_keys.append(key)

    if not written_keys:
        return json.dumps(
            {
                "error": "no values provided for requested fields",
                "missing": missing_keys,
                "fields": fields,
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "written": len(written_keys),
            "keys": written_keys,
            "missing": missing_keys,
            "case_id": str(deps.case_id),
            "status": "confirmed",
        },
        ensure_ascii=False,
    )


async def case_slot_collect(
    ctx: RunContext[AgentDeps],
    fields_json: str,
    values: dict[str, str] | None = None,
) -> str:
    """Ask the user to fill missing Case archive slots via a HITL form.

    Provide fields_json as a JSON array of {key, label, unit?, reason?}.
    After approval the UI supplies values={{key: string}}; do not invent values.
    """

    return await run_case_slot_collect(
        ctx.deps,
        fields_json=fields_json,
        values=cast(dict[str, object] | None, values),
    )


__all__ = [
    "case_slot_collect",
    "run_case_slot_collect",
]
