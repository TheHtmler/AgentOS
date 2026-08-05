import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_ai import Agent, ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent_api.agent import AgentOutput
from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.agent_store import AgentNotFoundError, get_published_version
from agent_api.db.chat_store import (
    ThreadBusyError,
    ThreadNotFoundError,
    append_text_delta,
    cancel_run,
    complete_run,
    fail_run,
    list_completed_run_message_histories,
    list_thread_messages,
    start_run,
)
from agent_api.db.models import Message, Thread, User
from agent_api.db.session import session_factory
from agent_api.memory.extract import schedule_memory_extract
from agent_api.memory.recall import format_memory_block, load_relevant_memories
from agent_api.output_limits import with_truncation_notice_if_needed
from agent_api.runtime import AgentRuntime, get_runtime
from agent_api.thread_title import schedule_auto_thread_title
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatStreamRequest(BaseModel):
    """Text input and an optional existing Thread for a durable conversation."""

    message: str = Field(max_length=4_000)
    thread_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank")

        return message


def requested_agent_id(request: Request) -> UUID | None:
    """Parse the Agent header used only when creating a new Thread."""

    raw = request.headers.get("X-AgentOS-Agent-Id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="X-AgentOS-Agent-Id must be a UUID",
        ) from error


def encode_sse_event(event: str, data: dict[str, str]) -> str:
    """Serialize JSON to prevent model output from breaking SSE line boundaries."""

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


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


async def persist_failed_run(run_id: UUID) -> None:
    """Commit a safe terminal state when model execution fails."""

    async with session_factory() as session, session.begin():
        await fail_run(session, run_id=run_id)


async def persist_cancelled_run(run_id: UUID) -> None:
    """Commit cancellation in a separate short transaction."""

    async with session_factory() as session, session.begin():
        await cancel_run(session, run_id=run_id)


def chunk_assistant_text(text: str, *, chunk_size: int = 32) -> list[str]:
    """Split completed assistant text into SSE-friendly fragments."""

    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


async def event_stream(
    request: Request,
    message: str,
    thread_id: UUID,
    run_id: UUID,
    user_id: UUID,
    agent_id: UUID,
    message_history: list[ModelMessage],
    runtime: AgentRuntime,
    agent: Agent[Any, AgentOutput],
) -> AsyncIterator[str]:
    """Run the full agent graph (including tools), then emit the final answer over SSE.

    `run_stream` can treat early text as the final result and stop before tool calls
    finish. `agent.run` always completes the tool loop first.
    """

    assistant_parts: list[str] = []
    model_messages: list[dict[str, object]] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_request_count: int | None = None

    try:
        # The lock protects the configured resource budget for the model's full execution.
        async with runtime.model_semaphore:
            if await request.is_disconnected():
                await persist_cancelled_run(run_id)
                return

            # Prefer run() over run_stream() so web_search and other tools finish
            # before any assistant text is treated as the final answer.
            result = await agent.run(
                message,
                message_history=message_history or None,
                conversation_id=str(thread_id),
                run_id=str(run_id),
                deps=AgentDeps(
                    search_router=runtime.search_router,
                    fetch_router=runtime.fetch_router,
                    run_id=run_id,
                ),
            )

            if await request.is_disconnected():
                await persist_cancelled_run(run_id)
                return

            new_messages = result.new_messages()
            # Classic SSE does not yet surface HITL cards; AG-UI owns approval UI.
            # Still pause durably so the Thread is not left running / double-booked.
            if not isinstance(result.output, str):
                from agent_api.hitl_pause import persist_deferred_approvals

                await persist_deferred_approvals(
                    run_id=run_id,
                    output=result.output,
                    model_messages=strip_thinking_parts(
                        parse_model_messages_json(result.new_messages_json()),
                    ),
                )
                if not await request.is_disconnected():
                    yield encode_sse_event(
                        "error",
                        {
                            "message": (
                                "此工具需要审批。请在主聊天界面（AG-UI）中批准或拒绝后继续。"
                            ),
                        },
                    )
                return

            output = with_truncation_notice_if_needed(result.output, new_messages)
            for delta in chunk_assistant_text(output):
                if await request.is_disconnected():
                    await persist_cancelled_run(run_id)
                    return

                await persist_text_delta(run_id, delta)
                assistant_parts.append(delta)
                yield encode_sse_event("text_delta", {"delta": delta})

            model_messages = strip_thinking_parts(
                parse_model_messages_json(result.new_messages_json()),
            )
            usage = result.usage
            input_tokens = usage.input_tokens or None
            output_tokens = usage.output_tokens or None
            model_request_count = usage.requests
    except asyncio.CancelledError:
        # A cancelled response must not leave the durable Run marked as running.
        try:
            await asyncio.shield(persist_cancelled_run(run_id))
        except Exception:
            logger.exception("Unable to persist cancelled run %s", run_id)
        raise
    except Exception:
        logger.exception("Chat stream failed for run %s", run_id)

        try:
            await persist_failed_run(run_id)
        except Exception:
            logger.exception("Unable to persist failed run %s", run_id)

        if not await request.is_disconnected():
            yield encode_sse_event(
                "error",
                {"message": "模型服务暂时不可用，请稍后重试。"},
            )
        return

    if await request.is_disconnected():
        await persist_cancelled_run(run_id)
        return

    assistant_content = "".join(assistant_parts)
    try:
        await persist_completed_run(
            run_id=run_id,
            assistant_content=assistant_content,
            model_messages=model_messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_request_count=model_request_count,
        )
    except Exception:
        logger.exception("Unable to complete run %s", run_id)

        if not await request.is_disconnected():
            yield encode_sse_event(
                "error",
                {"message": "对话记录保存失败，请稍后重试。"},
            )
        return

    # Title generation is async and must not delay the SSE done event.
    if runtime.ollama_http_client is not None:
        schedule_auto_thread_title(
            thread_id=thread_id,
            user_message=message,
            assistant_content=assistant_content,
            model_semaphore=runtime.model_semaphore,
            http_client=runtime.ollama_http_client,
        )
        schedule_memory_extract(
            user_id=user_id,
            agent_id=agent_id,
            thread_id=thread_id,
            run_id=run_id,
            user_message=message,
            assistant_content=assistant_content,
            model_semaphore=runtime.model_semaphore,
            http_client=runtime.ollama_http_client,
        )

    yield encode_sse_event("done", {})


@router.post("/stream")
async def stream_chat(
    payload: ChatStreamRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Create durable Run facts before opening the SSE response."""

    try:
        async with session_factory() as session, session.begin():
            agent_id = requested_agent_id(request) if payload.thread_id is None else None
            started = await start_run(
                session,
                thread_id=payload.thread_id,
                user_content=payload.message,
                model_name=get_settings().ollama_model,
                user_id=user.id,
                agent_id=agent_id,
            )
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error
    except AgentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    except ThreadBusyError as error:
        raise HTTPException(status_code=409, detail="Thread is already running") from error

    try:
        message_history = await load_thread_model_history(started.thread_id, user_id=user.id)
    except (ThreadNotFoundError, ValidationError) as error:
        logger.exception("Unable to load model history for thread %s", started.thread_id)
        raise HTTPException(
            status_code=500,
            detail="Conversation history is unavailable",
        ) from error

    async with session_factory() as session:
        thread = await session.get(Thread, started.thread_id)
        if thread is None:
            raise RuntimeError(f"Thread {started.thread_id} disappeared after starting its run")
        version = await get_published_version(session, thread.agent_id)
        memory_block = None
        if version.memory_enabled:
            try:
                memories = await load_relevant_memories(
                    session,
                    user_id=user.id,
                    agent_id=thread.agent_id,
                    message=payload.message,
                    top_k=get_settings().memory_recall_top_k,
                    max_chars=get_settings().memory_recall_max_chars,
                )
                memory_block = format_memory_block(memories)
            except Exception:
                logger.exception("memory recall failed; continuing without memories")
    runtime = get_runtime(request)
    agent = runtime.build_run_agent(
        system_prompt_overlay=version.system_prompt_overlay,
        tool_policy_overrides=version.tool_policy_overrides,
        memory_block=memory_block,
    )

    return StreamingResponse(
        event_stream(
            request,
            payload.message,
            started.thread_id,
            started.run_id,
            user.id,
            thread.agent_id,
            message_history,
            runtime,
            agent,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-AgentOS-Thread-ID": str(started.thread_id),
            "X-AgentOS-Run-ID": str(started.run_id),
        },
    )
