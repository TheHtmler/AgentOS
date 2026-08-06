"""Read-only tool for confirmed Case facts bound to the current Thread."""

from __future__ import annotations

import json
import logging

from pydantic_ai import RunContext

from agent_api.db.case_store import list_confirmed_facts
from agent_api.db.session import session_factory
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


async def run_case_context_read(
    deps: AgentDeps,
    *,
    query: str | None = None,
) -> str:
    """Return confirmed Case facts; optional query filters by substring/key/tag."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("case_context_read")
    if blocked is not None:
        return blocked

    if deps.case_id is None:
        return json.dumps(
            {"error": "No Case is bound to this conversation"},
            ensure_ascii=False,
        )

    try:
        async with session_factory() as session:
            facts = await list_confirmed_facts(session, case_id=deps.case_id)
    except Exception as exc:
        logger.exception("case_context_read failed")
        return json.dumps(
            {"error": f"case context read failed: {exc}"},
            ensure_ascii=False,
        )

    needle = (query or "").strip().lower()
    items: list[dict[str, object]] = []
    for fact in facts:
        payload = {
            "key": fact.key,
            "content": fact.content,
            "tags": list(fact.tags or []),
        }
        if needle:
            haystack = " ".join(
                [
                    fact.key or "",
                    fact.content,
                    " ".join(fact.tags or []),
                ],
            ).lower()
            if needle not in haystack:
                continue
        items.append(payload)

    return json.dumps(
        {
            "case_id": str(deps.case_id),
            "count": len(items),
            "facts": items,
            "note": "Confirmed Case facts only; do not invent missing slots.",
        },
        ensure_ascii=False,
    )


async def case_context_read(
    ctx: RunContext[AgentDeps],
    query: str | None = None,
) -> str:
    """Read confirmed facts from the Case archive bound to this conversation.

    Optional query filters by key, tag, or content substring.
    """

    return await run_case_context_read(ctx.deps, query=query)
