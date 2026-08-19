"""Continue a Run after HITL decisions using DeferredToolResults."""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import (
    DeferredToolApprovalResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.usage import UsageLimits

from agent_api.agent import AgentOutput, build_context_snapshot, inject_context_snapshot
from agent_api.api.chat import (
    format_run_failure_message,
    parse_model_messages_json,
    persist_completed_run,
    persist_failed_run,
    strip_thinking_parts,
)
from agent_api.case.extract import schedule_case_extract
from agent_api.case.recall import load_case_injection
from agent_api.config import get_settings
from agent_api.context_budget import (
    apply_context_budget,
    run_with_overflow_retry,
)
from agent_api.db.agent_store import get_published_version
from agent_api.db.chat_store import get_run, get_run_message_history, list_thread_messages
from agent_api.db.models import Interrupt, Thread
from agent_api.db.provider_store import ModelProviderUnavailableError, resolve_model_profile
from agent_api.db.session import session_factory
from agent_api.hitl_pause import persist_deferred_approvals
from agent_api.memory.extract import schedule_memory_extract
from agent_api.memory.recall import format_memory_block, load_relevant_memories
from agent_api.output_limits import with_truncation_notice_if_needed
from agent_api.runtime import AgentRuntime
from agent_api.thread_title import schedule_auto_thread_title
from agent_api.tools.search.tool import AgentDeps
from agent_api.uploads.context import load_upload_injection

logger = logging.getLogger(__name__)


def deferred_results_from_interrupts(interrupts: list[Interrupt]) -> DeferredToolResults:
    """Map resolved interrupt rows to Pydantic AI deferred approval results."""

    approvals: dict[str, DeferredToolApprovalResult | bool] = {}
    for item in interrupts:
        if item.status == "approved":
            # tool_args may include merged override values from the approve path.
            override = dict(item.tool_args) if item.tool_args else None
            approvals[item.tool_call_id] = ToolApproved(override_args=override)
        else:
            message = item.decision_message or "The tool call was denied."
            approvals[item.tool_call_id] = ToolDenied(message=message)
    return DeferredToolResults(approvals=approvals)


async def continue_run_after_approval(
    runtime: AgentRuntime,
    *,
    run_id: UUID,
    user_id: UUID,
    interrupts: list[Interrupt],
) -> None:
    """Execute the deferred-tool resume path and persist the next terminal (or pause) state."""

    async with session_factory() as session:
        run = await get_run(session, run_id=run_id, user_id=user_id)
        history_raw = await get_run_message_history(session, run_id=run_id)
        thread = await session.get(Thread, run.thread_id)
        version = (
            await get_published_version(session, thread.agent_id) if thread is not None else None
        )
        prompt = ""
        memory_block = None
        case_block = None
        upload_block = None
        case_keys: set[str] = set()
        case_id = thread.case_id if thread is not None else None
        settings = get_settings()
        profile = None
        if version is not None:
            try:
                profile = await resolve_model_profile(session, version, settings)
            except ModelProviderUnavailableError:
                logger.exception("model provider unavailable for resume run_id=%s", run_id)
        if thread is not None and version is not None:
            messages = await list_thread_messages(
                session,
                thread_id=thread.id,
                user_id=user_id,
            )
            prompt = next(
                (message.content for message in reversed(messages) if message.role == "user"),
                "",
            )
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
                        user_id=user_id,
                        agent_id=thread.agent_id,
                        case_id=case_id,
                        message=prompt,
                        top_k=settings.memory_recall_top_k,
                        max_chars=settings.memory_recall_max_chars,
                        http_client=runtime.ollama_http_client,
                    )
                    memory_block = format_memory_block(memories, exclude_keys=case_keys)
                except Exception:
                    logger.exception("memory recall failed; continuing without memories")
            try:
                upload_block = await load_upload_injection(
                    session,
                    owner_user_id=user_id,
                    case_id=case_id,
                    user_text=prompt,
                )
            except Exception:
                logger.exception("upload context failed; continuing without artifact preview")

    if thread is None or version is None or profile is None:
        logger.error("Missing thread for resume run_id=%s", run_id)
        await persist_failed_run(run_id)
        return

    if history_raw is None:
        logger.error("Missing message history checkpoint for resume run_id=%s", run_id)
        await persist_failed_run(run_id)
        return

    try:
        message_history = ModelMessagesTypeAdapter.validate_python(history_raw)
    except Exception:
        logger.exception("Invalid message history checkpoint for run_id=%s", run_id)
        await persist_failed_run(run_id)
        return

    deferred = deferred_results_from_interrupts(interrupts)
    snapshot = build_context_snapshot(
        memory_block=memory_block,
        case_block=case_block,
        upload_block=upload_block,
        timezone_name=settings.runtime_timezone,
        locale=settings.runtime_locale,
    )
    message_history, budget_report = apply_context_budget(
        message_history,
        context_window=profile.context_window,
        output_reserve=profile.max_output_tokens,
        snapshot_text=snapshot,
        user_text=prompt,
    )
    budget_report.log(run_id=run_id)
    # Prepend on resume: appending would split the checkpoint's trailing tool pair.
    message_history = inject_context_snapshot(message_history, snapshot, position="start")
    agent = runtime.build_run_agent(
        system_prompt_overlay=version.system_prompt_overlay,
        tool_policy_overrides=version.tool_policy_overrides,
        case_bound=case_id is not None,
        model_profile=profile,
    )

    try:
        async with runtime.semaphore_for_profile(profile):
            # Omit pydantic-ai run_id: the checkpoint already contains the interrupted
            # attempt's id; reusing it raises UserError. Our DB run_id stays the same.
            async def _run(history: list[ModelMessage] | None) -> AgentRunResult[AgentOutput]:
                return await agent.run(
                    message_history=history,
                    deferred_tool_results=deferred,
                    conversation_id=str(run.thread_id),
                    usage_limits=UsageLimits(request_limit=settings.agent_max_requests_per_run),
                    deps=AgentDeps(
                        search_router=runtime.search_router,
                        fetch_router=runtime.fetch_router,
                        run_id=run_id,
                        case_id=case_id,
                        user_id=user_id,
                        thread_id=run.thread_id,
                        http_client=runtime.ollama_http_client,
                        knowledge_base_slugs=version.knowledge_base_slugs,
                    ),
                )

            result = await run_with_overflow_retry(
                _run,
                message_history,
                run_id=run_id,
            )
    except Exception as error:
        logger.exception("HITL resume model run failed for run_id=%s", run_id)
        await persist_failed_run(
            run_id,
            error_message=format_run_failure_message(error),
        )
        return

    new_messages = result.new_messages()
    model_messages = strip_thinking_parts(
        parse_model_messages_json(
            ModelMessagesTypeAdapter.dump_json([*message_history, *new_messages]),
        ),
    )

    if isinstance(result.output, DeferredToolRequests) and result.output.approvals:
        await persist_deferred_approvals(
            run_id=run_id,
            output=result.output,
            model_messages=model_messages,
        )
        return

    if not isinstance(result.output, str):
        logger.error("Unexpected resume output type for run_id=%s: %s", run_id, type(result.output))
        await persist_failed_run(run_id)
        return

    assistant_content = with_truncation_notice_if_needed(result.output, new_messages)
    usage = result.usage
    await persist_completed_run(
        run_id=run_id,
        assistant_content=assistant_content,
        model_messages=model_messages,
        input_tokens=usage.input_tokens or None,
        output_tokens=usage.output_tokens or None,
        model_request_count=usage.requests,
    )

    if runtime.ollama_http_client is not None:
        schedule_auto_thread_title(
            thread_id=run.thread_id,
            user_message="(continued after approval)",
            assistant_content=assistant_content,
            model_semaphore=runtime.model_semaphore,
            http_client=runtime.ollama_http_client,
        )
        schedule_memory_extract(
            user_id=user_id,
            agent_id=thread.agent_id,
            thread_id=run.thread_id,
            run_id=run_id,
            user_message=prompt,
            assistant_content=assistant_content,
            model_semaphore=runtime.model_semaphore,
            http_client=runtime.ollama_http_client,
            memory_enabled=version.memory_enabled,
            case_id=case_id,
        )
        schedule_case_extract(
            user_id=user_id,
            case_id=case_id,
            case_enabled=version.case_enabled,
            thread_id=run.thread_id,
            run_id=run_id,
            user_message=prompt,
            assistant_content=assistant_content,
            model_semaphore=runtime.model_semaphore,
            http_client=runtime.ollama_http_client,
        )


def start_resume_background(
    runtime: AgentRuntime,
    *,
    run_id: UUID,
    user_id: UUID,
    interrupts: list[Interrupt],
) -> None:
    """Keep resume execution alive independent of the resume HTTP response."""

    runtime.start_background_run(
        run_id,
        continue_run_after_approval(
            runtime,
            run_id=run_id,
            user_id=user_id,
            interrupts=interrupts,
        ),
    )
