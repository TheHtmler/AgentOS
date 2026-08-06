"""Built-in child growth assessment (WHO 2006 via anthro; NHC WS/T 423-2022 via SD tables)."""

from __future__ import annotations

import json
import logging
import math
from datetime import date
from typing import Any, cast
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)

WHO_SOURCE_URL = "https://www.who.int/tools/child-growth-standards"
SUPPORTED_STANDARDS = frozenset({"who-2006", "nhc-wst-423-2022"})
STANDARD_ALIASES: dict[str, str] = {
    "who-2006": "who-2006",
    "nhc-wst-423-2022": "nhc-wst-423-2022",
    "nhc": "nhc-wst-423-2022",
    "nhc-2022": "nhc-wst-423-2022",
}
DAYS_PER_MONTH = 365.25 / 12.0


def z_to_percentile(z: float) -> float:
    """Approximate cumulative normal percentile for a z-score."""

    return round(100.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), 1)


def _normalize_standard(standard: str) -> str | None:
    key = standard.strip().lower()
    if not key:
        return "who-2006"
    return STANDARD_ALIASES.get(key)


def _normalize_sex(sex: str) -> str | None:
    value = sex.strip().lower()
    if value in {"m", "male", "boy", "男", "男孩"}:
        return "male"
    if value in {"f", "female", "girl", "女", "女孩"}:
        return "female"
    return None


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _indicator_payload(z: object, label: str) -> dict[str, Any] | None:
    z_f = _as_optional_float(z)
    if z_f is None:
        return None
    return {
        "indicator": label,
        "z_score": round(z_f, 2),
        "percentile": z_to_percentile(z_f),
    }


def _resolve_age_months(
    *,
    age_months: float | None,
    date_of_birth: str | None,
    measured_on: str | None,
) -> float | None:
    if age_months is not None:
        return float(age_months)
    if not date_of_birth or not measured_on:
        return None
    try:
        dob = date.fromisoformat(date_of_birth.strip())
        measured = date.fromisoformat(measured_on.strip())
    except ValueError:
        return None
    days = (measured - dob).days
    if days < 0:
        return None
    return days / DAYS_PER_MONTH


async def run_growth_assess(
    deps: AgentDeps,
    *,
    sex: str,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    age_months: float | None = None,
    date_of_birth: str | None = None,
    measured_on: str | None = None,
    standard: str = "who-2006",
) -> str:
    """Compute growth z-scores/percentiles for unit tests and the tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("growth_assess")
    if blocked is not None:
        return blocked

    std = _normalize_standard(standard)
    if std is None or std not in SUPPORTED_STANDARDS:
        return json.dumps(
            {
                "error": (
                    f"Unsupported standard '{standard}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_STANDARDS))} "
                    "(aliases: nhc → nhc-wst-423-2022)."
                ),
                "supported_standards": sorted(SUPPORTED_STANDARDS),
            },
            ensure_ascii=False,
        )

    normalized_sex = _normalize_sex(sex)
    if normalized_sex is None:
        return json.dumps(
            {"error": "sex must be male/female (or 男/女)"},
            ensure_ascii=False,
        )

    if height_cm is None and weight_kg is None:
        return json.dumps(
            {"error": "Provide at least height_cm or weight_kg"},
            ensure_ascii=False,
        )

    resolved_age = _resolve_age_months(
        age_months=age_months,
        date_of_birth=date_of_birth,
        measured_on=measured_on,
    )
    has_age_months = age_months is not None
    has_dob_pair = bool(date_of_birth and measured_on)
    if resolved_age is None and not has_age_months and not has_dob_pair:
        return json.dumps(
            {
                "error": (
                    "Provide age_months, or both date_of_birth and measured_on "
                    "(YYYY-MM-DD)"
                ),
            },
            ensure_ascii=False,
        )
    if resolved_age is None:
        return json.dumps(
            {"error": "Invalid date_of_birth or measured_on (use YYYY-MM-DD)"},
            ensure_ascii=False,
        )

    payload: dict[str, object] = {"sex": normalized_sex}
    if has_dob_pair:
        payload["dob"] = date_of_birth
        payload["measured"] = measured_on
    else:
        payload["age_months"] = float(age_months)  # type: ignore[arg-type]
    if height_cm is not None:
        payload["height_cm"] = float(height_cm)
    if weight_kg is not None:
        payload["weight_kg"] = float(weight_kg)

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, payload)

    if std == "nhc-wst-423-2022":
        return await _run_nhc_assess(
            deps,
            sex=cast(Any, normalized_sex),
            age_months=resolved_age,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )

    return await _run_who_assess(
        deps,
        payload=payload,
        height_cm=height_cm,
        weight_kg=weight_kg,
    )


async def _run_nhc_assess(
    deps: AgentDeps,
    *,
    sex: str,
    age_months: float,
    height_cm: float | None,
    weight_kg: float | None,
) -> str:
    from agent_api.tools.growth.nhc import assess_nhc

    try:
        result = assess_nhc(
            sex=cast(Any, sex),
            age_months=age_months,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )
    except Exception as exc:
        logger.exception("growth_assess NHC compute failed")
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(deps.run_id, ok=False, summary=str(exc)[:500], provider="nhc")
        return json.dumps({"error": f"growth computation failed: {exc}"}, ensure_ascii=False)

    indicators = [
        {
            "indicator": row.indicator,
            "z_score": row.z_score,
            "percentile": row.percentile,
        }
        for row in result.indicators
    ]

    response: dict[str, object] = {
        "standard": result.standard,
        "standard_version": result.standard_version,
        "source_url": result.source_url,
        "sex": result.sex,
        "age_months": result.age_months,
        "height_cm": result.height_cm,
        "weight_kg": result.weight_kg,
        "indicators": indicators,
        "warnings": result.warnings,
        "errors": result.errors,
        "note": (
            "Educational NHC WS/T 423-2022 assessment (7 years and under). "
            "Not a clinical diagnosis; cite source_url when explaining results."
        ),
    }

    if deps.persist_tool_events and deps.run_id is not None:
        summary = ", ".join(
            f"{row['indicator']} p{row['percentile']}" for row in indicators[:3]
        ) or "no indicators"
        await _persist_tool_result(deps.run_id, ok=True, summary=summary[:500], provider="nhc")

    return json.dumps(response, ensure_ascii=False)


async def _run_who_assess(
    deps: AgentDeps,
    *,
    payload: dict[str, object],
    height_cm: float | None,
    weight_kg: float | None,
) -> str:
    try:
        import importlib

        anthro_mod = cast(Any, importlib.import_module("anthro"))
        result = cast(dict[str, object], anthro_mod.compute(payload))
    except Exception as exc:
        logger.exception("growth_assess compute failed")
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(deps.run_id, ok=False, summary=str(exc)[:500], provider="anthro")
        return json.dumps({"error": f"growth computation failed: {exc}"}, ensure_ascii=False)

    indicators = [
        item
        for item in (
            _indicator_payload(result.get("z_lhfa"), "length_height_for_age"),
            _indicator_payload(result.get("z_wfa"), "weight_for_age"),
            _indicator_payload(result.get("z_wflh"), "weight_for_length_height"),
            _indicator_payload(result.get("z_bmi"), "bmi_for_age"),
        )
        if item is not None
    ]

    response: dict[str, object] = {
        "standard": "who-2006",
        "source_url": WHO_SOURCE_URL,
        "sex": result.get("sex"),
        "age_months": result.get("age_months"),
        "age_days": result.get("age_days"),
        "height_cm": result.get("height_cm_raw") or height_cm,
        "weight_kg": result.get("weight_kg") or weight_kg,
        "indicators": indicators,
        "warnings": result.get("warnings") or [],
        "errors": result.get("errors") or [],
        "note": (
            "Educational WHO 2006 assessment (approx. 0–5 years). "
            "Not a clinical diagnosis; cite source_url when explaining results."
        ),
    }

    if deps.persist_tool_events and deps.run_id is not None:
        summary = ", ".join(
            f"{row['indicator']} p{row['percentile']}" for row in indicators[:3]
        ) or "no indicators"
        await _persist_tool_result(deps.run_id, ok=True, summary=summary[:500], provider="anthro")

    return json.dumps(response, ensure_ascii=False)


async def growth_assess(
    ctx: RunContext[AgentDeps],
    sex: str,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    age_months: float | None = None,
    date_of_birth: str | None = None,
    measured_on: str | None = None,
    standard: str = "who-2006",
) -> str:
    """Assess child growth vs WHO 2006 or NHC WS/T 423-2022 (z-score and percentile).

    Prefer this over web_search when the user provides height/weight and age or DOB.
    Use standard='nhc' or 'nhc-wst-423-2022' for the China NHC standard.
    """

    return await run_growth_assess(
        ctx.deps,
        sex=sex,
        height_cm=height_cm,
        weight_kg=weight_kg,
        age_months=age_months,
        date_of_birth=date_of_birth,
        measured_on=measured_on,
        standard=standard,
    )


async def _persist_tool_call(run_id: UUID, args: dict[str, object]) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="growth_assess",
                args=args,
            )
    except Exception:
        logger.exception("Unable to persist growth_assess tool_call run=%s", run_id)


async def _persist_tool_result(
    run_id: UUID,
    *,
    ok: bool,
    summary: str,
    provider: str = "anthro",
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="growth_assess",
                provider=provider,
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist growth_assess tool_result run=%s", run_id)
