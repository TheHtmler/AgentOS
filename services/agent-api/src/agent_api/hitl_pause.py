"""Persist DeferredToolRequests as waiting_approval + pending interrupts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic_ai.tools import DeferredToolRequests

from agent_api.config import get_settings
from agent_api.db.chat_store import pause_run_for_approval
from agent_api.db.session import session_factory
from agent_api.hitl_types import ApprovalRequest


def _tool_args(call: Any) -> dict[str, object]:
    if hasattr(call, "args_as_dict"):
        raw = call.args_as_dict()
        if isinstance(raw, dict):
            return {str(key): value for key, value in cast(dict[object, object], raw).items()}
    args = getattr(call, "args", None)
    if isinstance(args, dict):
        return {str(key): value for key, value in cast(dict[object, object], args).items()}
    if isinstance(args, str) and args.strip():
        return {"raw": args}
    return {}


async def persist_deferred_approvals(
    *,
    run_id: UUID,
    output: DeferredToolRequests,
    model_messages: list[dict[str, object]],
) -> None:
    """Pause the Run when the model requested one or more deferred approvals."""

    if not output.approvals:
        raise ValueError("DeferredToolRequests.approvals is empty")

    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.hitl_approval_timeout_seconds)
    approvals = [
        ApprovalRequest(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name or "unknown",
            tool_args=_tool_args(call),
        )
        for call in output.approvals
    ]
    async with session_factory() as session, session.begin():
        await pause_run_for_approval(
            session,
            run_id=run_id,
            approvals=approvals,
            model_messages=model_messages,
            expires_at=expires_at,
        )
