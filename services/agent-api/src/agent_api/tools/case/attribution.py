"""Deferred HITL tool: confirm writing proposed Case facts to the bound Case."""

from __future__ import annotations

import json

from pydantic_ai import RunContext

from agent_api.case.extract import CaseFactUpdate, apply_case_extract, parse_case_extract_payload
from agent_api.db.case_store import user_can_write_case
from agent_api.db.session import session_factory
from agent_api.tools.search.tool import AgentDeps


async def run_case_attribution_confirm(
    deps: AgentDeps,
    *,
    updates_json: str,
    attribution: str = "self",
) -> str:
    """Persist approved Case updates as confirmed after HITL approval."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("case_attribution_confirm")
    if blocked is not None:
        return blocked

    if deps.case_id is None or deps.user_id is None:
        return json.dumps(
            {"error": "No Case is bound to this conversation"},
            ensure_ascii=False,
        )

    try:
        raw_updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "updates_json must be valid JSON"}, ensure_ascii=False)

    # Approval already means the user attributed these facts to the bound Case.
    _ = attribution
    payload = parse_case_extract_payload({"attribution": "self", "updates": raw_updates})
    if not payload.updates:
        return json.dumps({"error": "no valid updates", "written": 0}, ensure_ascii=False)

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
        written = await apply_case_extract(
            session,
            user_id=deps.user_id,
            case_id=deps.case_id,
            payload=payload,
            source_thread_id=None,
            source_run_id=deps.run_id,
        )
    return json.dumps(
        {
            "written": written,
            "case_id": str(deps.case_id),
            "status": "confirmed",
        },
        ensure_ascii=False,
    )


async def case_attribution_confirm(
    ctx: RunContext[AgentDeps],
    updates_json: str,
    attribution: str = "unknown",
) -> str:
    """Ask the user whether listed Case facts should be written to the current archive.

    Call when attribution is unclear. Provide updates_json as a JSON array of
    {key, content, tags}. Requires approval before writing confirmed facts.
    """

    _ = attribution  # Surfaced in the approval card via tool args.
    return await run_case_attribution_confirm(
        ctx.deps,
        updates_json=updates_json,
        attribution="self",
    )


# Re-export for tests that build updates without going through JSON.
__all__ = [
    "CaseFactUpdate",
    "case_attribution_confirm",
    "run_case_attribution_confirm",
]
