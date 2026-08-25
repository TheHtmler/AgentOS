import asyncio
import json
import logging
from typing import cast
from uuid import UUID

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from agent_api.config import get_settings
from agent_api.context_budget import (
    BudgetReport,
    is_context_overflow_error,
)
from agent_api.db.chat_store import (
    append_context_budget_event,
    append_model_step_event,
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


def resolve_version_tuning(version_value: int | None, env_default: int) -> int:
    """AgentVersion tuning fields are NULL-inherited from the env default."""

    return version_value if version_value is not None else env_default


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
    """Keep raw reasoning private while preserving Responses turn continuity.

    OpenAI Responses providers may require the opaque ``id``/``signature`` pair
    on the next request. It is safe to retain that metadata without retaining
    readable summaries or provider-specific raw reasoning.
    """

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
                        part_id = part_dict.get("id")
                        signature = part_dict.get("signature")
                        if (
                            isinstance(part_id, str)
                            and part_id
                            and isinstance(signature, str)
                            and signature
                        ):
                            continuation_part: dict[str, object] = {
                                "part_kind": "thinking",
                                "content": "",
                                "id": part_id,
                                "signature": signature,
                            }
                            provider_name = part_dict.get("provider_name")
                            if isinstance(provider_name, str) and provider_name:
                                continuation_part["provider_name"] = provider_name
                            sanitized_parts.append(continuation_part)
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


async def load_thread_model_history(
    thread_id: UUID,
    *,
    user_id: UUID,
    history_max_runs: int | None = None,
) -> list[ModelMessage]:
    """Load server-authored history for the next model turn.

    Prefer ``run_message_histories`` snapshots. If they are missing or empty (for example
    after a partial persist failure), fall back to completed user/assistant Message rows.
    Callers invoke this after ``start_run``, so the newest trailing user Message belongs to
    the in-progress turn and must be omitted from the fallback history.
    ``history_max_runs`` is the caller-resolved window (AgentVersion tuning or env).
    """

    max_runs = resolve_version_tuning(history_max_runs, get_settings().history_max_runs)

    async with session_factory() as session:
        snapshots = await list_completed_run_message_histories(
            session,
            thread_id=thread_id,
            limit=max_runs,
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

    # Keep the same run window as the resolved max_runs (each run ~= user+assistant pair).
    max_messages = max_runs * 2
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


async def persist_model_step_event(
    run_id: UUID,
    *,
    duration_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    """Record total run wall-clock + token usage for the Ops timeline; best-effort only.

    Coarse (whole-run from request-start to completion, not per internal
    tool-loop model request — the AG-UI adapter doesn't expose that
    boundary) but still real signal: subtracting the interleaved
    tool_result events' own duration_ms from this total approximates time
    actually spent waiting on the model. Must never fail the run itself
    over an observability write.
    """

    try:
        async with session_factory() as session, session.begin():
            await append_model_step_event(
                session,
                run_id=run_id,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
    except Exception:
        logger.exception("Unable to persist model_step event for run %s", run_id)


async def persist_context_budget_event(
    run_id: UUID,
    report: BudgetReport,
    *,
    phase: str,
) -> None:
    """Persist one budget-trim fact for the Ops timeline; best-effort only.

    No-op when the guard did nothing, so call sites stay one line. Must never
    fail the run itself over an observability write.
    """

    if not report.actions:
        return
    try:
        async with session_factory() as session, session.begin():
            await append_context_budget_event(
                session,
                run_id=run_id,
                phase=phase,
                history_before_tokens=report.history_before_tokens,
                history_after_tokens=report.history_after_tokens,
                budget_tokens=report.budget_tokens,
                pruned_chars=report.pruned_chars,
                dropped_runs=report.dropped_runs,
                actions=report.actions,
                summary=report.summary(),
            )
    except Exception:
        logger.exception("Unable to persist context_budget event for run %s", run_id)


_context_budget_event_tasks: set[asyncio.Task[None]] = set()


def schedule_context_budget_event(run_id: UUID, report: BudgetReport, *, phase: str) -> None:
    """Fire-and-forget persist from the sync per-step history processor.

    The processor runs inside the agent's event loop in production; unit tests
    may drive it without one — then there is simply no loop to schedule on.
    """

    if not report.actions:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(persist_context_budget_event(run_id, report, phase=phase))
    _context_budget_event_tasks.add(task)
    task.add_done_callback(_context_budget_event_tasks.discard)


def format_run_failure_message(error: BaseException, *, limit: int = 500) -> str:
    """Store a short, non-secret failure hint on the Run row for later diagnosis.

    Provider errors often arrive wrapped in an ExceptionGroup ("unhandled errors
    in a TaskGroup"), which says nothing on its own — append the deepest real
    error so the Ops timeline shows the actual cause.
    """

    text = f"{type(error).__name__}: {error}".strip()
    chain = _error_chain(error)
    leaves = [item for item in chain if not isinstance(item, BaseExceptionGroup)]
    root = leaves[-1]
    root_text = f"{type(root).__name__}: {root}".strip()
    if root is not error and root_text != text:
        text = f"{text} (root cause: {root_text})"
    if not text or text == ":":
        return "Agent model failed."
    return text[:limit]


def _error_chain(error: BaseException) -> list[BaseException]:
    """Walk chained provider errors, unwrapping ExceptionGroup members.

    Model calls run inside TaskGroups, so the real provider error often arrives
    wrapped in an ExceptionGroup whose members are not linked via ``__cause__`` —
    without unwrapping, timeout/overload/config diagnosis all degrades to the
    generic fallback.
    """

    chain: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if isinstance(current, BaseExceptionGroup):
            stack.extend(cast("BaseExceptionGroup[BaseException]", current).exceptions)
        linked = current.__cause__ or current.__context__
        if linked is not None:
            stack.append(linked)
    return chain


def _is_model_timeout(error: BaseException) -> bool:
    timeout_names = {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutError",
        "TimeoutException",
        "WriteTimeout",
    }
    return any(
        isinstance(item, TimeoutError) or type(item).__name__ in timeout_names
        for item in _error_chain(error)
    )


def _is_model_overloaded(error: BaseException) -> bool:
    return any("overloaded" in str(item).lower() for item in _error_chain(error))


def _is_non_json_endpoint_response(error: BaseException) -> bool:
    """Endpoint answered with a web page or an empty stream instead of API data.

    Classic symptom of a base_url / api_mode mismatch (e.g. the gateway only
    serves the API under /v1 while base_url points at the site root, so the web
    console's catch-all answers with HTML). Non-streamed chat mode surfaces it
    as a non-JSON response; the streaming path surfaces it as an empty stream.
    """

    for item in _error_chain(error):
        if not isinstance(item, UnexpectedModelBehavior):
            continue
        message = str(item)
        if "expected JSON data" in message or "Streamed response ended without content" in message:
            return True
    return False


def user_facing_run_error_message(error: BaseException) -> str:
    """Map provider failures to actionable user-facing text instead of a generic 500."""

    if _is_non_json_endpoint_response(error):
        return (
            "模型端点返回了无法解析的应答（可能是网页或空响应），通常是 Provider 的 base_url "
            "与 API 模式不匹配（例如端点只服务 /v1 前缀）。请到 Ops 检查 Provider 配置；"
            "配置无误则稍后重试。"
        )
    if is_context_overflow_error(error):
        return (
            "当前内容超出了模型上下文窗口（16k tokens)。"
            "建议一次分析一份报告，或另起一段对话后重试。"
        )
    if isinstance(error, UsageLimitExceeded):
        return "本轮操作步数过多，已自动停止。请重新提问或换个更具体的说法再试。"
    if _is_model_timeout(error):
        return "上游模型响应超时，请稍后重试；如果连续出现，请检查 Provider 地址或端点状态。"
    if _is_model_overloaded(error):
        return "上游模型当前过载，请稍后重试。"
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
