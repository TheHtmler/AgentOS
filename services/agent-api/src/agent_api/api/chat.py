import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.chat_store import (
    ThreadBusyError,
    ThreadNotFoundError,
    append_text_delta,
    cancel_run,
    complete_run,
    fail_run,
    list_completed_run_message_histories,
    start_run,
)
from agent_api.db.models import User
from agent_api.db.session import session_factory
from agent_api.runtime import AgentRuntime, get_runtime
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


async def load_thread_model_history(thread_id: UUID, *, user_id: UUID) -> list[ModelMessage]:
    """Load only server-authored, completed model-message blocks for one Thread."""

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

    return history


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


async def event_stream(
    request: Request,
    message: str,
    thread_id: UUID,
    run_id: UUID,
    message_history: list[ModelMessage],
    runtime: AgentRuntime,
) -> AsyncIterator[str]:
    """Stream model output while writing each durable fact in short transactions."""

    assistant_parts: list[str] = []

    try:
        # The lock protects the configured resource budget for the model's full execution.
        async with runtime.model_semaphore:
            if await request.is_disconnected():
                await persist_cancelled_run(run_id)
                return

            async with runtime.agent.run_stream(
                message,
                message_history=message_history or None,
                conversation_id=str(thread_id),
                run_id=str(run_id),
                deps=AgentDeps(
                    search_router=runtime.search_router,
                    run_id=run_id,
                ),
            ) as result:
                async for delta in result.stream_text(delta=True, debounce_by=None):
                    if await request.is_disconnected():
                        await persist_cancelled_run(run_id)
                        return

                    if delta:
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

    try:
        await persist_completed_run(
            run_id=run_id,
            assistant_content="".join(assistant_parts),
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
            started = await start_run(
                session,
                thread_id=payload.thread_id,
                user_content=payload.message,
                model_name=get_settings().ollama_model,
                user_id=user.id,
            )
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error
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

    return StreamingResponse(
        event_stream(
            request,
            payload.message,
            started.thread_id,
            started.run_id,
            message_history,
            get_runtime(request),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-AgentOS-Thread-ID": str(started.thread_id),
            "X-AgentOS-Run-ID": str(started.run_id),
        },
    )
