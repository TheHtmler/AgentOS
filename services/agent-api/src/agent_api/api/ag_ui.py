import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from ag_ui.core import RunAgentInput, UserMessage
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from agent_api.api.chat import (
    load_thread_model_history,
    parse_model_messages_json,
    persist_cancelled_run,
    persist_completed_run,
    persist_failed_run,
    persist_text_delta,
)
from agent_api.config import get_settings
from agent_api.db.chat_store import ThreadBusyError, ThreadNotFoundError, start_run
from agent_api.db.session import session_factory
from agent_api.runtime import get_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ag-ui", tags=["ag-ui"])


class AGUIExecutionError(RuntimeError):
    """An error message that is safe to expose through an AG-UI RUN_ERROR event."""


def current_user_message(run_input: RunAgentInput) -> tuple[UserMessage, str]:
    if not run_input.messages or not isinstance(run_input.messages[-1], UserMessage):
        raise HTTPException(status_code=422, detail="The final message must be a user message")

    message = run_input.messages[-1]
    if not isinstance(message.content, str):
        raise HTTPException(status_code=422, detail="Only text user messages are supported")

    content = message.content.strip()
    if not content or len(content) > 4_000:
        raise HTTPException(status_code=422, detail="Message must contain 1 to 4000 characters")

    return message, content


def requested_thread_id(thread_id: str) -> UUID | None:
    if thread_id == "new":
        return None

    try:
        return UUID(thread_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="threadId must be a UUID or 'new'") from error


def text_from_native_event(event: NativeEvent) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content or None

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta or None

    return None


@router.post("/runs")
async def stream_ag_ui_run(request: Request):
    """Run AG-UI while keeping message history and durable Run ownership on the server."""

    try:
        client_input = RunAgentInput.model_validate_json(await request.body())
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid AG-UI request") from error

    user_message, prompt = current_user_message(client_input)

    try:
        async with session_factory() as session, session.begin():
            started = await start_run(
                session,
                thread_id=requested_thread_id(client_input.thread_id),
                user_content=prompt,
                model_name=get_settings().ollama_model,
            )
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error
    except ThreadBusyError as error:
        raise HTTPException(status_code=409, detail="Thread is already running") from error

    try:
        history = await load_thread_model_history(started.thread_id)
    except (ThreadNotFoundError, ValidationError) as error:
        logger.exception("Unable to load model history for thread %s", started.thread_id)
        raise HTTPException(
            status_code=500, detail="Conversation history is unavailable"
        ) from error

    # Ignore browser-supplied history, state, tools, and run identity.
    server_input = client_input.model_copy(
        update={
            "thread_id": str(started.thread_id),
            "run_id": str(started.run_id),
            "state": {},
            "messages": [user_message],
            "tools": [],
            "context": [],
            "forwarded_props": {},
        },
    )
    adapter = AGUIAdapter(
        agent=get_runtime(request).agent,
        run_input=server_input,
        accept=request.headers.get("accept"),
    )

    async def native_events() -> AsyncIterator[NativeEvent]:
        try:
            async with get_runtime(request).model_semaphore:
                async for event in adapter.run_stream_native(
                    message_history=history,
                    conversation_id=str(started.thread_id),
                    run_id=str(started.run_id),
                ):
                    if await request.is_disconnected():
                        await persist_cancelled_run(started.run_id)
                        return

                    if text := text_from_native_event(event):
                        await persist_text_delta(started.run_id, text)

                    yield event
        except asyncio.CancelledError:
            await asyncio.shield(persist_cancelled_run(started.run_id))
            raise
        except Exception as error:
            logger.exception("AG-UI stream failed for run %s", started.run_id)
            await persist_failed_run(started.run_id)
            raise AGUIExecutionError("模型服务暂时不可用，请稍后重试。") from error

    async def persist_completed(result: AgentRunResult[str]) -> None:
        try:
            usage = result.usage
            await persist_completed_run(
                run_id=started.run_id,
                assistant_content=result.output,
                model_messages=parse_model_messages_json(
                    ModelMessagesTypeAdapter.dump_json(
                        [*adapter.messages, *result.new_messages()],
                    ),
                ),
                input_tokens=usage.input_tokens or None,
                output_tokens=usage.output_tokens or None,
                model_request_count=usage.requests,
            )
        except Exception as error:
            logger.exception("Unable to complete AG-UI run %s", started.run_id)
            await persist_failed_run(started.run_id)
            raise AGUIExecutionError("对话记录保存失败，请稍后重试。") from error

    response = adapter.streaming_response(
        adapter.transform_stream(native_events(), on_complete=persist_completed),
    )
    response.headers.update(
        {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-AgentOS-Thread-ID": str(started.thread_id),
            "X-AgentOS-Run-ID": str(started.run_id),
        },
    )
    return response
