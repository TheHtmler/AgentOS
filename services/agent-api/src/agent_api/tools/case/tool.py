"""Read-only tool for confirmed Case facts bound to the current Thread."""

from __future__ import annotations

import json
import logging

from pydantic_ai import RunContext

from agent_api.case.recall import (
    current_facts_by_key,
    format_recorded_at,
    history_excluding_current,
)
from agent_api.config import get_settings
from agent_api.db.case_store import list_confirmed_facts, list_keyed_fact_history
from agent_api.db.session import session_factory
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


async def run_case_context_read(
    deps: AgentDeps,
    *,
    query: str | None = None,
    include_history: bool = True,
) -> str:
    """Return Case facts with recorded_at; history is prior values only."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("case_context_read")
    if blocked is not None:
        return blocked

    if deps.case_id is None:
        return json.dumps(
            {"error": "No Case is bound to this conversation"},
            ensure_ascii=False,
        )

    settings = get_settings()
    try:
        async with session_factory() as session:
            facts = await list_confirmed_facts(session, case_id=deps.case_id)
            history = (
                await list_keyed_fact_history(session, case_id=deps.case_id)
                if include_history
                else []
            )
    except Exception as exc:
        logger.exception("case_context_read failed")
        return json.dumps(
            {"error": f"case context read failed: {exc}"},
            ensure_ascii=False,
        )

    needle = (query or "").strip().lower()

    def _match(content: str, key: str | None, tags: list[str]) -> bool:
        if not needle:
            return True
        haystack = " ".join([key or "", content, " ".join(tags)]).lower()
        return needle in haystack

    current_facts = current_facts_by_key(facts)
    current_items: list[dict[str, object]] = []
    for fact in current_facts:
        tags = list(fact.tags or [])
        if not _match(fact.content, fact.key, tags):
            continue
        current_items.append(
            {
                "key": fact.key,
                "content": fact.content,
                "tags": tags,
                "status": fact.status,
                "recorded_at": format_recorded_at(
                    fact.updated_at or fact.created_at,
                    timezone_name=settings.runtime_timezone,
                ),
            },
        )

    prior = history_excluding_current(history, current_facts) if include_history else []
    history_items: list[dict[str, object]] = []
    for fact in prior:
        tags = list(fact.tags or [])
        if not _match(fact.content, fact.key, tags):
            continue
        history_items.append(
            {
                "key": fact.key,
                "content": fact.content,
                "tags": tags,
                "status": fact.status,
                "recorded_at": format_recorded_at(
                    fact.updated_at or fact.created_at,
                    timezone_name=settings.runtime_timezone,
                ),
            },
        )

    return json.dumps(
        {
            "case_id": str(deps.case_id),
            "current_count": len(current_items),
            "current": current_items,
            "history_count": len(history_items),
            "history": history_items,
            "note": (
                "Use current for 目前/现在. history is prior values only "
                "(empty means 暂无更早记录 — do not repeat current). "
                "Do not invent missing timestamps."
            ),
        },
        ensure_ascii=False,
    )


async def case_context_read(
    ctx: RunContext[AgentDeps],
    query: str | None = None,
    include_history: bool = True,
) -> str:
    """Read Case facts for this conversation (current + optional keyed history).

    Optional query filters by key, tag, or content substring.
    """

    return await run_case_context_read(
        ctx.deps,
        query=query,
        include_history=include_history,
    )
