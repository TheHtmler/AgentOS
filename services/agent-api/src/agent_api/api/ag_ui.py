import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from ag_ui.core import BaseEvent, RunAgentInput, UserMessage
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from agent_api.agent import AgentOutput
from agent_api.api.auth import get_current_user
from agent_api.api.chat import (
    load_thread_model_history,
    parse_model_messages_json,
    persist_cancelled_run,
    persist_completed_run,
    persist_failed_run,
    persist_text_delta,
    strip_thinking_parts,
)
from agent_api.config import get_settings
from agent_api.db.agent_store import (
    AgentNotFoundError,
    PublishedAgentVersionNotFoundError,
    get_published_version,
)
from agent_api.db.case_store import CaseNotFoundError
from agent_api.db.chat_store import ThreadBusyError, ThreadNotFoundError, start_run
from agent_api.db.models import Thread, User
from agent_api.db.session import session_factory
from agent_api.hitl_pause import persist_deferred_approvals
from agent_api.case.extract import schedule_case_extract
from agent_api.case.recall import load_case_injection
from agent_api.memory.extract import schedule_memory_extract
from agent_api.memory.recall import format_memory_block, load_relevant_memories
from agent_api.output_limits import with_truncation_notice_if_needed
from agent_api.runtime import get_runtime
from agent_api.thread_title import schedule_auto_thread_title
from agent_api.tools.search.tool import AgentDeps

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


def requested_case_id(request: Request) -> UUID | None:
    """Parse optional Case override used only when creating a new Thread."""

    raw = request.headers.get("X-AgentOS-Case-Id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="X-AgentOS-Case-Id must be a UUID",
        ) from error


def text_from_native_event(event: NativeEvent) -> str | None:
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content or None

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta or None

    return None


@router.post("/runs")
async def stream_ag_ui_run(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    """Run AG-UI while keeping message history and durable Run ownership on the server."""

    try:
        client_input = RunAgentInput.model_validate_json(await request.body())
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid AG-UI request") from error

    user_message, prompt = current_user_message(client_input)
    thread_id = requested_thread_id(client_input.thread_id)

    try:
        async with session_factory() as session, session.begin():
            agent_id = requested_agent_id(request) if thread_id is None else None
            case_id_header = requested_case_id(request) if thread_id is None else None
            started = await start_run(
                session,
                thread_id=thread_id,
                user_content=prompt,
                model_name=get_settings().ollama_model,
                user_id=user.id,
                agent_id=agent_id,
                case_id=case_id_header,
            )
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error
    except AgentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case not found") from error
    except ThreadBusyError as error:
        raise HTTPException(status_code=409, detail="Thread is already running") from error

    try:
        history = await load_thread_model_history(started.thread_id, user_id=user.id)
        async with session_factory() as session:
            thread = await session.get(Thread, started.thread_id)
            if thread is None:
                raise RuntimeError(f"Thread {started.thread_id} disappeared after starting its run")
            version = await get_published_version(session, thread.agent_id)
            memory_block = None
            case_block = None
            case_keys: set[str] = set()
            case_id = thread.case_id
            runtime = get_runtime(request)
            settings = get_settings()
            if version.case_enabled and case_id is not None:
                try:
                    case_block, case_keys = await load_case_injection(
                        session,
                        case_id=case_id,
                        timezone_name=settings.runtime_timezone,
                    )
                except Exception:
                    logger.exception("case recall failed; continuing without case block")
            if version.memory_enabled:
                try:
                    memories = await load_relevant_memories(
                        session,
                        user_id=user.id,
                        agent_id=thread.agent_id,
                        message=prompt,
                        top_k=settings.memory_recall_top_k,
                        max_chars=settings.memory_recall_max_chars,
                        http_client=runtime.ollama_http_client,
                    )
                    memory_block = format_memory_block(memories, exclude_keys=case_keys)
                except Exception:
                    logger.exception("memory recall failed; continuing without memories")

        agent = runtime.build_run_agent(
            system_prompt_overlay=version.system_prompt_overlay,
            tool_policy_overrides=version.tool_policy_overrides,
            memory_block=memory_block,
            case_block=case_block,
            case_bound=case_id is not None,
        )
    except PublishedAgentVersionNotFoundError as error:
        logger.exception("Agent version unavailable for run %s", started.run_id)
        await persist_failed_run(started.run_id)
        raise HTTPException(
            status_code=409,
            detail="Agent configuration has no published version",
        ) from error
    except (ThreadNotFoundError, ValidationError) as error:
        logger.exception("Unable to prepare AG-UI run %s", started.run_id)
        await persist_failed_run(started.run_id)
        raise HTTPException(
            status_code=500,
            detail="Conversation history is unavailable",
        ) from error
    except Exception as error:
        logger.exception("Unable to prepare AG-UI run %s", started.run_id)
        await persist_failed_run(started.run_id)
        raise HTTPException(
            status_code=500,
            detail="Unable to start agent execution",
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
        agent=agent,
        run_input=server_input,
        accept=request.headers.get("accept"),
    )
    event_queue: asyncio.Queue[BaseEvent | BaseException | None] = asyncio.Queue()
    client_disconnected = asyncio.Event()

    async def native_events() -> AsyncIterator[NativeEvent]:
        try:
            # The model task is independent from the HTTP response. A mobile browser may
            # suspend its page and close SSE while the server should still finish the Run.
            async with runtime.model_semaphore:
                async for event in adapter.run_stream_native(
                    message_history=history,
                    conversation_id=str(started.thread_id),
                    run_id=str(started.run_id),
                    deps=AgentDeps(
                        search_router=runtime.search_router,
                        fetch_router=runtime.fetch_router,
                        run_id=started.run_id,
                        case_id=case_id,
                        http_client=runtime.ollama_http_client,
                    ),
                ):
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

    async def persist_completed(result: AgentRunResult[AgentOutput]) -> None:
        try:
            usage = result.usage
            new_messages = result.new_messages()
            model_messages = strip_thinking_parts(
                parse_model_messages_json(
                    ModelMessagesTypeAdapter.dump_json(
                        [*adapter.messages, *new_messages],
                    ),
                ),
            )
            if isinstance(result.output, DeferredToolRequests) and result.output.approvals:
                await persist_deferred_approvals(
                    run_id=started.run_id,
                    output=result.output,
                    model_messages=model_messages,
                )
                return

            if not isinstance(result.output, str):
                raise AGUIExecutionError("模型返回了无法处理的输出类型。")

            # Surface max_tokens cuts so users know to continue or raise the limit.
            assistant_content = with_truncation_notice_if_needed(
                result.output,
                new_messages,
            )
            await persist_completed_run(
                run_id=started.run_id,
                assistant_content=assistant_content,
                model_messages=model_messages,
                input_tokens=usage.input_tokens or None,
                output_tokens=usage.output_tokens or None,
                model_request_count=usage.requests,
            )
            # Same fire-and-forget path as classic SSE chat.
            if runtime.ollama_http_client is not None:
                schedule_auto_thread_title(
                    thread_id=started.thread_id,
                    user_message=prompt,
                    assistant_content=assistant_content,
                    model_semaphore=runtime.model_semaphore,
                    http_client=runtime.ollama_http_client,
                )
                schedule_memory_extract(
                    user_id=user.id,
                    agent_id=thread.agent_id,
                    thread_id=started.thread_id,
                    run_id=started.run_id,
                    user_message=prompt,
                    assistant_content=assistant_content,
                    model_semaphore=runtime.model_semaphore,
                    http_client=runtime.ollama_http_client,
                    memory_enabled=version.memory_enabled,
                )
                schedule_case_extract(
                    case_id=case_id,
                    case_enabled=version.case_enabled,
                    thread_id=started.thread_id,
                    run_id=started.run_id,
                    user_message=prompt,
                    assistant_content=assistant_content,
                    model_semaphore=runtime.model_semaphore,
                    http_client=runtime.ollama_http_client,
                )
        except AGUIExecutionError:
            raise
        except Exception as error:
            logger.exception("Unable to complete AG-UI run %s", started.run_id)
            await persist_failed_run(started.run_id)
            raise AGUIExecutionError("对话记录保存失败，请稍后重试。") from error

    async def produce_events() -> None:
        try:
            async for event in adapter.transform_stream(
                native_events(),
                on_complete=persist_completed,
            ):
                if not client_disconnected.is_set():
                    await event_queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("AG-UI background run failed for run %s", started.run_id)
            if not client_disconnected.is_set():
                await event_queue.put(error)
        finally:
            if not client_disconnected.is_set():
                await event_queue.put(None)

    async def stream_events() -> AsyncIterator[BaseEvent]:
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    return

                if await request.is_disconnected():
                    client_disconnected.set()
                    return

                if isinstance(event, BaseException):
                    raise event

                yield event
        except asyncio.CancelledError:
            client_disconnected.set()
            raise
        finally:
            client_disconnected.set()

    runtime.start_background_run(
        started.run_id,
        produce_events(),
    )
    response = adapter.streaming_response(stream_events())
    response.headers.update(
        {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-AgentOS-Thread-ID": str(started.thread_id),
            "X-AgentOS-Run-ID": str(started.run_id),
        },
    )
    return response
