"""The model-facing plan update tool.

Plan updates are deliberately side-effect free. They are persisted as normal
tool events so the existing AG-UI stream and history projection can replay them.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import RunContext

from agent_api.tools.search.tool import AgentDeps


def _normalize_steps(steps: list[str]) -> list[str]:
    normalized = [step.strip() for step in steps if step.strip()]
    if not normalized or len(normalized) > 12:
        raise ValueError("steps must contain between 1 and 12 non-empty items")
    if any(len(step) > 160 for step in normalized):
        raise ValueError("each plan step must be at most 160 characters")
    return normalized


async def update_plan(
    ctx: RunContext[AgentDeps],
    steps: list[str],
    active_index: int,
) -> str:
    """Publish a concise, user-visible plan and return its normalized snapshot."""

    normalized = _normalize_steps(steps)
    clamped = max(0, min(active_index, len(normalized)))
    payload: dict[str, Any] = {"steps": normalized, "activeIndex": clamped}

    if ctx.deps.persist_tool_events and ctx.deps.run_id is not None:
        from agent_api.db.chat_store import append_tool_call_event, append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=ctx.deps.run_id,
                tool_name="update_plan",
                args=payload,
            )
            await append_tool_result_event(
                session,
                run_id=ctx.deps.run_id,
                tool_name="update_plan",
                provider=None,
                ok=True,
                summary=f"Plan updated ({clamped}/{len(normalized)})",
                metadata={"plan": payload},
                result=json.dumps(payload, ensure_ascii=False),
            )

    return json.dumps(payload, ensure_ascii=False)
