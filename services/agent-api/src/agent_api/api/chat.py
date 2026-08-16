import json
import logging
from typing import cast
from uuid import UUID

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from agent_api.config import get_settings
from agent_api.context_budget import (
    is_context_overflow_error,
)
from agent_api.db.chat_store import (
    append_text_delta,
    cancel_run,
    complete_run,
    fail_run,
    list_completed_run_message_histories,
    list_thread_messages,
)
from agent_api.db.models import Message
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)


def parse_model_messages_json(raw_messages: bytes) -> list[dict[str, object]]:
    """Validate the server-generated Pydantic AI JSON before it reaches PostgreSQL."""

    decoded: object = json.loads(raw_messages)
    if not isinstance(decoded, list):
        raise RuntimeError("Pydantic AI returned an invalid message history.")

    messages: list[dict[str, object]] = []
    for item in cast(list[object], decoded):
        if not isinstance(item, dict):
            raise RuntimeError("Pydantic AI returned an invalid message history.")

        messages.append(cast(dict[str, object], item))

    return messages


def strip_thinking_parts(
    model_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Keep private reasoning out of durable model history and future prompts."""

    sanitized_messages: list[dict[str, object]] = []

    for message in model_messages:
        sanitized_message = message.copy()
        parts = message.get("parts")

        if isinstance(parts, list):
            sanitized_parts: list[object] = []
            for part in cast(list[object], parts):
                if isinstance(part, dict):
                    part_dict = cast(dict[str, object], part)
                    if part_dict.get("part_kind") == "thinking":
                        continue
                    sanitized_parts.append(part_dict)
                    continue
                sanitized_parts.append(part)
            sanitized_message["parts"] = sanitized_parts

        sanitized_messages.append(sanitized_message)

    return sanitized_messages


def model_history_from_thread_messages(rows: list[Message]) -> list[ModelMessage]:
    """Rebuild a minimal prompt history from durable user/assistant Message rows."""

    history: list[ModelMessage] = []
    pending_user: str | None = None

    for row in rows:
        if row.role == "user":
            if pending_user is not None:
                history.append(ModelRequest(parts=[UserPromptPart(content=pending_user)]))
            pending_user = row.content
            continue

        if row.role != "assistant":
            continue

        if pending_user is not None:
            history.append(ModelRequest(parts=[UserPromptPart(content=pending_user)]))
            pending_user = None

        history.append(ModelResponse(parts=[TextPart(content=row.content)]))

    return history


async def load_thread_model_history(thread_id: UUID, *, user_id: UUID) -> list[ModelMessage]:
    """Load server-authored history for the next model turn.

    Prefer ``run_message_histories`` snapshots. If they are missing or empty (for example
    after a partial persist failure), fall back to completed user/assistant Message rows.
    Callers invoke this after ``start_run``, so the newest trailing user Message belongs to
    the in-progress turn and must be omitted from the fallback history.
    """

    async with session_factory() as session:
        snapshots = await list_completed_run_message_histories(
            session,
            thread_id=thread_id,
            limit=get_settings().history_max_runs,
            user_id=user_id,
        )

        history: list[ModelMessage] = []
        for snapshot in snapshots:
            history.extend(ModelMessagesTypeAdapter.validate_python(snapshot.messages))

        if history:
            return history

        rows = await list_thread_messages(session, thread_id=thread_id, user_id=user_id)

    if rows and rows[-1].role == "user":
        rows = rows[:-1]

    # Keep the same run window as HISTORY_MAX_RUNS (each run ~= user+assistant pair).
    max_messages = get_settings().history_max_runs * 2
    if len(rows) > max_messages:
        rows = rows[-max_messages:]

    return model_history_from_thread_messages(rows)


async def persist_text_delta(run_id: UUID, delta: str) -> None:
    """Commit one emitted fragment before it becomes visible to the browser."""

    async with session_factory() as session, session.begin():
        await append_text_delta(session, run_id=run_id, delta=delta)


async def persist_completed_run(
    run_id: UUID,
    assistant_content: str,
    model_messages: list[dict[str, object]],
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model_request_count: int | None = None,
) -> None:
    """Commit the final assistant message and completed Run state together."""

    async with session_factory() as session, session.begin():
        await complete_run(
            session,
            run_id=run_id,
            assistant_content=assistant_content,
            model_messages=model_messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_request_count=model_request_count,
        )


def format_run_failure_message(error: BaseException, *, limit: int = 500) -> str:
    """Store a short, non-secret failure hint on the Run row for later diagnosis."""

    text = f"{type(error).__name__}: {error}".strip()
    if not text or text == ":":
        return "Agent model failed."
    return text[:limit]


def user_facing_run_error_message(error: BaseException) -> str:
    """Map provider failures to actionable user-facing text instead of a generic 500."""

    if is_context_overflow_error(error):
        return (
            "当前内容超出了模型上下文窗口（16k tokens)。"
            "建议一次分析一份报告，或另起一段对话后重试。"
        )
    return "模型服务暂时不可用，请稍后重试。"


async def persist_failed_run(
    run_id: UUID,
    *,
    error_message: str = "Agent model failed.",
) -> None:
    """Commit a safe terminal state when model execution fails."""

    async with session_factory() as session, session.begin():
        await fail_run(session, run_id=run_id, error_message=error_message)


async def persist_cancelled_run(run_id: UUID) -> None:
    """Commit cancellation in a separate short transaction."""

    async with session_factory() as session, session.begin():
        await cancel_run(session, run_id=run_id)
