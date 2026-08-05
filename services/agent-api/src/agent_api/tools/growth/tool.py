"""Built-in child growth assessment against WHO 2006 standards (via anthro)."""

from __future__ import annotations

import json
import logging
import math
from typing import Any, cast
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)

SUPPORTED_STANDARDS = frozenset({"who-2006"})
SOURCE_URL = "https://www.who.int/tools/child-growth-standards"


def z_to_percentile(z: float) -> float:
    """Approximate cumulative normal percentile for a z-score."""

    return round(100.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), 1)


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
    """Compute WHO growth z-scores/percentiles for unit tests and the tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("growth_assess")
    if blocked is not None:
        return blocked

    std = standard.strip().lower() or "who-2006"
    if std not in SUPPORTED_STANDARDS:
        return json.dumps(
            {
                "error": (
                    f"Unsupported standard '{standard}'. "
                    "MVP supports only 'who-2006' (WHO Child Growth Standards 0–5y)."
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

    has_age_months = age_months is not None
    has_dob_pair = bool(date_of_birth and measured_on)
    if not has_age_months and not has_dob_pair:
        return json.dumps(
            {
                "error": (
                    "Provide age_months, or both date_of_birth and measured_on "
                    "(YYYY-MM-DD)"
                ),
            },
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

    try:
        # anthro ships without type stubs; import via importlib to keep pyright quiet.
        import importlib

        anthro_mod = cast(Any, importlib.import_module("anthro"))
        result = cast(dict[str, object], anthro_mod.compute(payload))
    except Exception as exc:
        logger.exception("growth_assess compute failed")
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(deps.run_id, ok=False, summary=str(exc)[:500])
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
        "source_url": SOURCE_URL,
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
        await _persist_tool_result(deps.run_id, ok=True, summary=summary[:500])

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
    """Assess child growth vs WHO 2006 standards (z-score and percentile).

    Prefer this over web_search when the user provides height/weight and age or DOB.
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


async def _persist_tool_result(run_id: UUID, *, ok: bool, summary: str) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="growth_assess",
                provider="anthro",
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist growth_assess tool_result run=%s", run_id)
