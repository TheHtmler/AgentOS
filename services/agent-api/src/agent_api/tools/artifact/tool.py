"""Read-only windowed access to persisted Artifacts."""

from __future__ import annotations

import json
import logging
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


def clamp_read_max_chars(value: int | None) -> int:
    settings = get_settings()
    hard_max = settings.read_artifact_max_chars
    requested = hard_max if value is None else value
    return max(500, min(hard_max, requested))


async def run_read_artifact(
    deps: AgentDeps,
    artifact_id: str,
    offset: int = 0,
    max_chars: int | None = None,
) -> str:
    """Slice a user-owned artifact; missing/unauthorized look identical."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("read_artifact")
    if blocked is not None:
        return blocked

    settings = get_settings()
    if not settings.artifact_enabled:
        return json.dumps(
            {"error": "read_artifact is disabled", "code": "artifact_disabled"},
            ensure_ascii=False,
        )

    if deps.user_id is None:
        return json.dumps(
            {"error": "artifact not found", "code": "artifact_not_found"},
            ensure_ascii=False,
        )

    normalized = artifact_id.strip()
    try:
        artifact_uuid = UUID(normalized)
    except ValueError:
        return json.dumps(
            {"error": "artifact not found", "code": "artifact_not_found"},
            ensure_ascii=False,
        )

    start = max(0, int(offset))
    limit = clamp_read_max_chars(max_chars)

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, normalized, start, limit)

    try:
        from agent_api.db.artifact_store import get_owned_artifact
        from agent_api.db.session import session_factory

        async with session_factory() as session:
            row = await get_owned_artifact(
                session,
                artifact_id=artifact_uuid,
                owner_user_id=deps.user_id,
            )
    except Exception as exc:
        logger.exception("read_artifact failed for %s", normalized)
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(deps.run_id, ok=False, summary=str(exc)[:500])
        return json.dumps(
            {"error": "unable to read artifact", "code": "artifact_read_failed"},
            ensure_ascii=False,
        )

    if row is None:
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(
                deps.run_id,
                ok=False,
                summary="artifact not found",
            )
        return json.dumps(
            {"error": "artifact not found", "code": "artifact_not_found"},
            ensure_ascii=False,
        )

    total = row.content_chars
    slice_text = row.content[start : start + limit]
    truncated = start + len(slice_text) < total
    next_offset = start + len(slice_text) if truncated else None
    payload = {
        "artifact_id": str(row.id),
        "title": row.title,
        "source_url": row.source_url,
        "offset": start,
        "text": slice_text,
        "truncated": truncated,
        "total_chars": total,
        "next_offset": next_offset,
    }
    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_result(
            deps.run_id,
            ok=True,
            summary=f"{row.title}@{start}+{len(slice_text)}/{total}"[:500],
        )
    return json.dumps(payload, ensure_ascii=False)


async def read_artifact(
    ctx: RunContext[AgentDeps],
    artifact_id: str,
    offset: int = 0,
    max_chars: int | None = None,
) -> str:
    """Read a previously stored Artifact by id (offset window; owner-scoped)."""

    return await run_read_artifact(ctx.deps, artifact_id, offset, max_chars)


async def _persist_tool_call(
    run_id: UUID,
    artifact_id: str,
    offset: int,
    max_chars: int,
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="read_artifact",
                args={
                    "artifact_id": artifact_id,
                    "offset": offset,
                    "max_chars": max_chars,
                },
            )
    except Exception:
        logger.exception("Unable to persist tool_call for run %s", run_id)


async def _persist_tool_result(run_id: UUID, *, ok: bool, summary: str) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="read_artifact",
                provider="artifact",
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist tool_result for run %s", run_id)
