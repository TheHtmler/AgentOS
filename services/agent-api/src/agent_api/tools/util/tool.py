"""Pydantic AI entry points for platform util tools."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.search.tool import AgentDeps
from agent_api.tools.util.calculate import compute_calculate
from agent_api.tools.util.time_diff import compute_time_diff

logger = logging.getLogger(__name__)


async def run_calculate(deps: AgentDeps, *, expression: str) -> str:
    """Evaluate a restricted arithmetic expression for tests and the tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("calculate")
    if blocked is not None:
        return blocked

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, "calculate", {"expression": expression})

    payload = compute_calculate(expression)

    if deps.persist_tool_events and deps.run_id is not None:
        summary = (
            f"result={payload.get('result')}"
            if payload.get("ok")
            else f"error={payload.get('error_code')}"
        )
        await _persist_tool_result(
            deps.run_id,
            "calculate",
            ok=bool(payload.get("ok")),
            summary=summary[:500],
        )

    return json.dumps(payload, ensure_ascii=False)


async def run_time_diff(
    deps: AgentDeps,
    *,
    start: str,
    end: str | None = None,
    timezone: str | None = None,
    units: list[str] | None = None,
) -> str:
    """Compute a signed time delta for tests and the tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("time_diff")
    if blocked is not None:
        return blocked

    settings = get_settings()
    args: dict[str, object] = {"start": start}
    if end is not None:
        args["end"] = end
    if timezone is not None:
        args["timezone"] = timezone
    if units is not None:
        args["units"] = units

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, "time_diff", args)

    payload = compute_time_diff(
        start=start,
        end=end,
        timezone=timezone,
        units=units,
        default_timezone=settings.runtime_timezone,
    )

    if deps.persist_tool_events and deps.run_id is not None:
        summary = (
            f"delta={payload.get('delta')}"
            if payload.get("ok")
            else f"error={payload.get('error_code')}"
        )
        await _persist_tool_result(
            deps.run_id,
            "time_diff",
            ok=bool(payload.get("ok")),
            summary=summary[:500],
        )

    return json.dumps(payload, ensure_ascii=False)


async def calculate(ctx: RunContext[AgentDeps], expression: str) -> str:
    """Evaluate a safe arithmetic expression (whitelist AST; no side effects).

    Prefer this over mental math for exact totals, rates, and unitless formulas.
    """

    return await run_calculate(ctx.deps, expression=expression)


async def time_diff(
    ctx: RunContext[AgentDeps],
    start: str,
    end: str | None = None,
    timezone: str | None = None,
    units: list[str] | None = None,
) -> str:
    """Compute signed time deltas (days/hours/minutes/months/years).

    Omit end to use the Runtime Context Pack's authoritative "now". Prefer this
    over mental age/duration math. Dates without times are local midnight.
    """

    return await run_time_diff(
        ctx.deps,
        start=start,
        end=end,
        timezone=timezone,
        units=units,
    )


async def _persist_tool_call(run_id: UUID, tool_name: str, args: dict[str, Any]) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name=tool_name,
                args=args,
            )
    except Exception:
        logger.exception("Unable to persist %s tool_call run=%s", tool_name, run_id)


async def _persist_tool_result(
    run_id: UUID,
    tool_name: str,
    *,
    ok: bool,
    summary: str,
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name=tool_name,
                provider="util",
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist %s tool_result run=%s", tool_name, run_id)
