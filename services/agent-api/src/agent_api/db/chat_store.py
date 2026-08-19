from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.agent_store import get_published_version, resolve_agent_for_new_thread
from agent_api.db.case_store import resolve_case_for_new_thread
from agent_api.db.models import (
    Interrupt,
    Message,
    Run,
    RunEvent,
    RunMessageHistory,
    Thread,
)
from agent_api.hitl_types import ApprovalRequest, InterruptDecision


class ThreadNotFoundError(LookupError):
    """Raised when a requested conversation does not exist."""


class ThreadBusyError(RuntimeError):
    """Raised when a Thread already has an active model execution."""


class RunNotFoundError(LookupError):
    """Raised when a requested agent execution does not exist."""


class InvalidRunStateError(ValueError):
    """Raised when a terminal update is attempted for a non-running Run."""


class InterruptDecisionError(ValueError):
    """Raised when resume decisions do not match the pending interrupt set."""


@dataclass(frozen=True)
class StartedRun:
    """Identifiers created together before model streaming begins."""

    thread_id: UUID
    run_id: UUID


@dataclass(frozen=True)
class ThreadListItem:
    """One recent conversation summary for the chat navigation."""

    id: UUID
    agent_id: UUID
    title: str | None
    latest_message_content: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ThreadToolCallItem:
    """A render-safe tool summary anchored after one user message."""

    id: UUID
    tool_name: str
    args: dict[str, object]
    status: str
    provider: str | None
    summary: str
    after_message_id: UUID


async def _create_thread(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    agent_id: UUID,
    case_id: UUID | None = None,
) -> Thread:
    thread = Thread(user_id=user_id, agent_id=agent_id, case_id=case_id)
    session.add(thread)
    await session.flush()
    return thread


async def create_empty_thread(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID | None = None,
    case_id: UUID | None = None,
) -> Thread:
    """Create a Thread with no messages (e.g. so the client can upload before chatting)."""

    resolved_agent_id = await resolve_agent_for_new_thread(session, agent_id)
    version = await get_published_version(session, resolved_agent_id)
    resolved_case_id = await resolve_case_for_new_thread(
        session,
        user_id=user_id,
        agent_id=resolved_agent_id,
        case_id=case_id,
        case_enabled=version.case_enabled,
    )
    thread = await _create_thread(
        session,
        user_id=user_id,
        agent_id=resolved_agent_id,
        case_id=resolved_case_id,
    )
    await session.refresh(thread)
    return thread


async def _get_active_thread(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID | None = None,
    for_update: bool = False,
) -> Thread:
    """Load a non-deleted Thread, optionally scoped to an owner."""

    statement = select(Thread).where(
        Thread.id == thread_id,
        Thread.deleted_at.is_(None),
    )
    if user_id is not None:
        statement = statement.where(Thread.user_id == user_id)
    if for_update:
        statement = statement.with_for_update()

    thread = await session.scalar(statement)
    if thread is None:
        raise ThreadNotFoundError(f"Thread {thread_id} does not exist")

    return thread


async def _lock_thread(session: AsyncSession, *, thread_id: UUID, user_id: UUID) -> Thread:
    # Locking the parent row serializes allocation of messages.seq within one thread.
    return await _get_active_thread(
        session,
        thread_id=thread_id,
        user_id=user_id,
        for_update=True,
    )


async def _ensure_thread_has_no_running_run(
    session: AsyncSession,
    thread_id: UUID,
) -> None:
    # waiting_approval also occupies the thread until the user resolves or cancels.
    active_run_id = await session.scalar(
        select(Run.id)
        .where(
            Run.thread_id == thread_id,
            Run.status.in_(("running", "waiting_approval")),
        )
        .limit(1),
    )
    if active_run_id is not None:
        raise ThreadBusyError(f"Thread {thread_id} already has a running Run")


async def _lock_run(session: AsyncSession, run_id: UUID) -> Run:
    run = await session.scalar(
        select(Run).where(Run.id == run_id).with_for_update(),
    )
    if run is None:
        raise RunNotFoundError(f"Run {run_id} does not exist")

    return run


async def _next_message_seq(session: AsyncSession, thread_id: UUID) -> int:
    last_seq = await session.scalar(
        select(func.coalesce(func.max(Message.seq), 0)).where(Message.thread_id == thread_id),
    )
    return (last_seq or 0) + 1


async def _next_run_event_seq(session: AsyncSession, run_id: UUID) -> int:
    last_seq = await session.scalar(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id),
    )
    return (last_seq or 0) + 1


async def get_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    user_id: UUID,
) -> Run:
    """Return one Run for server-side observability."""

    run = await session.scalar(
        select(Run)
        .join(Thread, Thread.id == Run.thread_id)
        .where(
            Run.id == run_id,
            Thread.user_id == user_id,
            Thread.deleted_at.is_(None),
        )
    )
    if run is None:
        raise RunNotFoundError(f"Run {run_id} does not exist")

    return run


async def list_thread_messages(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID | None = None,
) -> list[Message]:
    """Return final messages in their durable conversation order."""

    thread = await _get_active_thread(session, thread_id=thread_id, user_id=user_id)

    messages = await session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.seq),
    )
    return list(messages)


async def get_thread_latest_run(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID | None = None,
) -> Run | None:
    """Return the newest Run for a Thread the user can access (or None)."""

    thread = await _get_active_thread(session, thread_id=thread_id, user_id=user_id)
    return await session.scalar(
        select(Run).where(Run.thread_id == thread.id).order_by(Run.created_at.desc()).limit(1),
    )


async def list_thread_tool_calls(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID | None = None,
) -> list[ThreadToolCallItem]:
    """Return completed tool summaries for UI history, without secrets or full hits.

    Each ``start_run`` creates one user message and one Run. Tool events are anchored to
    that user message by pairing runs and user messages in creation order.
    """

    thread = await _get_active_thread(session, thread_id=thread_id, user_id=user_id)

    user_messages = list(
        (
            await session.scalars(
                select(Message)
                .where(
                    Message.thread_id == thread.id,
                    Message.role == "user",
                )
                .order_by(Message.seq),
            )
        ).all()
    )
    # Prefer started_at: created_at can collide for same-transaction inserts, and UUID
    # order does not match start_run order.
    runs = list(
        (
            await session.scalars(
                select(Run)
                .where(Run.thread_id == thread.id)
                .order_by(Run.started_at.asc().nulls_last(), Run.created_at, Run.id),
            )
        ).all()
    )

    if not user_messages or not runs:
        return []

    run_ids = [run.id for run in runs]
    events = list(
        (
            await session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id.in_(run_ids),
                    RunEvent.event_type.in_(("tool_call", "tool_result")),
                )
                .order_by(RunEvent.run_id, RunEvent.seq),
            )
        ).all()
    )
    events_by_run: dict[UUID, list[RunEvent]] = {}
    for event in events:
        events_by_run.setdefault(event.run_id, []).append(event)

    items: list[ThreadToolCallItem] = []
    for index, run in enumerate(runs):
        if index >= len(user_messages):
            break

        after_message_id = user_messages[index].id
        pending_calls: list[RunEvent] = []

        for event in events_by_run.get(run.id, []):
            if event.event_type == "tool_call":
                pending_calls.append(event)
                continue

            if event.event_type != "tool_result" or not pending_calls:
                continue

            call_event = pending_calls.pop(0)
            call_payload = call_event.payload
            result_payload = event.payload

            tool_name = str(call_payload.get("tool") or result_payload.get("tool") or "tool")
            raw_args = call_payload.get("args")
            args: dict[str, object] = {}
            if isinstance(raw_args, dict):
                typed_args = cast(dict[object, object], raw_args)
                args = {str(key): value for key, value in typed_args.items()}
            ok = result_payload.get("ok")
            provider_value = result_payload.get("provider")
            provider = provider_value if isinstance(provider_value, str) else None
            summary_value = result_payload.get("summary")
            summary = summary_value if isinstance(summary_value, str) else ""

            items.append(
                ThreadToolCallItem(
                    id=call_event.id,
                    tool_name=tool_name,
                    args=args,
                    status="done" if ok is True else "error",
                    provider=provider,
                    summary=summary[:500],
                    after_message_id=after_message_id,
                )
            )

    return items


async def list_threads(
    session: AsyncSession,
    *,
    limit: int,
    user_id: UUID,
    agent_id: UUID | None = None,
) -> list[ThreadListItem]:
    """Return recent Threads with a render-safe latest-message preview."""

    latest_message_content = (
        select(Message.content)
        .where(Message.thread_id == Thread.id)
        .order_by(Message.seq.desc())
        .limit(1)
        .scalar_subquery()
    )

    statement = select(Thread, latest_message_content).where(
        Thread.user_id == user_id,
        Thread.deleted_at.is_(None),
    )
    if agent_id is not None:
        statement = statement.where(Thread.agent_id == agent_id)

    result = await session.execute(
        statement.order_by(Thread.updated_at.desc(), Thread.id.desc()).limit(limit),
    )

    return [
        ThreadListItem(
            id=thread.id,
            agent_id=thread.agent_id,
            title=thread.title,
            latest_message_content=message_content,
            updated_at=thread.updated_at,
        )
        for thread, message_content in result.tuples()
    ]


async def rename_thread(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID,
    title: str | None,
) -> Thread:
    """Set or clear a Thread title for the owning user."""

    thread = await _get_active_thread(session, thread_id=thread_id, user_id=user_id)
    thread.title = title
    thread.updated_at = datetime.now(UTC)
    await session.flush()
    return thread


async def try_set_thread_title_if_empty(
    session: AsyncSession,
    *,
    thread_id: UUID,
    title: str,
) -> bool:
    """Set title only when still NULL so a concurrent manual rename wins.

    Returns True when the row was updated.
    """

    thread = await session.scalar(
        select(Thread)
        .where(
            Thread.id == thread_id,
            Thread.deleted_at.is_(None),
        )
        .with_for_update(),
    )
    # Skip missing/deleted threads and any title the user (or a prior job) already set.
    if thread is None or thread.title is not None:
        return False

    normalized = title.strip()
    if not normalized:
        return False

    thread.title = normalized[:255]
    thread.updated_at = datetime.now(UTC)
    await session.flush()
    return True


async def soft_delete_thread(
    session: AsyncSession,
    *,
    thread_id: UUID,
    user_id: UUID,
) -> bool:
    """Mark a Thread deleted. Returns False when already deleted or missing for this user."""

    thread = await session.scalar(
        select(Thread).where(
            Thread.id == thread_id,
            Thread.user_id == user_id,
        ),
    )
    if thread is None:
        raise ThreadNotFoundError(f"Thread {thread_id} does not exist")

    if thread.deleted_at is not None:
        return False

    deleted_at = datetime.now(UTC)
    thread.deleted_at = deleted_at
    thread.updated_at = deleted_at
    await session.flush()
    return True


async def list_completed_run_message_histories(
    session: AsyncSession,
    *,
    thread_id: UUID,
    limit: int,
    user_id: UUID | None = None,
) -> list[RunMessageHistory]:
    """Return recent complete model-message blocks in chronological order."""

    thread = await _get_active_thread(session, thread_id=thread_id, user_id=user_id)

    recent_run_ids = (
        select(RunMessageHistory.run_id.label("run_id"))
        .join(Run, RunMessageHistory.run_id == Run.id)
        .where(
            Run.thread_id == thread.id,
            Run.status == "completed",
        )
        .order_by(Run.completed_at.desc(), Run.id.desc())
        .limit(limit)
        .subquery()
    )

    histories = await session.scalars(
        select(RunMessageHistory)
        .join(Run, RunMessageHistory.run_id == Run.id)
        .join(recent_run_ids, RunMessageHistory.run_id == recent_run_ids.c.run_id)
        .order_by(Run.completed_at, Run.id),
    )
    return list(histories)


async def start_run(
    session: AsyncSession,
    *,
    thread_id: UUID | None,
    user_content: str,
    model_name: str,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
    case_id: UUID | None = None,
) -> StartedRun:
    """Record a user message and a running execution in the caller's transaction.

    HTTP handlers always supply ``user_id``. The optional value keeps isolated legacy repository
    tests usable while ownerless development records remain inaccessible through every API.
    New Threads on case-enabled Agents bind a Case (client override or lazy default).
    """

    if thread_id is None:
        resolved_agent_id = await resolve_agent_for_new_thread(session, agent_id)
        resolved_case_id: UUID | None = None
        if user_id is not None:
            version = await get_published_version(session, resolved_agent_id)
            resolved_case_id = await resolve_case_for_new_thread(
                session,
                user_id=user_id,
                agent_id=resolved_agent_id,
                case_id=case_id,
                case_enabled=version.case_enabled,
            )
        thread = await _create_thread(
            session,
            user_id=user_id,
            agent_id=resolved_agent_id,
            case_id=resolved_case_id,
        )
    else:
        # Existing Threads retain their Agent to preserve conversation isolation.
        if user_id is None:
            thread = await _get_active_thread(
                session,
                thread_id=thread_id,
                for_update=True,
            )
        else:
            thread = await _lock_thread(session, thread_id=thread_id, user_id=user_id)
        await _ensure_thread_has_no_running_run(session, thread.id)
    message = Message(
        thread_id=thread.id,
        seq=await _next_message_seq(session, thread.id),
        role="user",
        content=user_content,
    )
    run = Run(
        thread_id=thread.id,
        case_id=thread.case_id,
        status="running",
        model_name=model_name,
        started_at=datetime.now(UTC),
    )
    session.add_all([message, run])
    await session.flush()

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_started",
        payload={"status": "running"},
    )

    return StartedRun(thread_id=thread.id, run_id=run.id)


async def update_run_model_name(
    session: AsyncSession,
    *,
    run_id: UUID,
    model_name: str,
) -> None:
    """Correct a run's model_name once the version's provider profile is resolved.

    ``start_run`` records a placeholder before the Thread's published version
    (and therefore its model provider) is known; the run row should reflect the
    model that actually executes.
    """

    run = await session.get(Run, run_id)
    if run is not None:
        run.model_name = model_name


async def append_run_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    event_type: str,
    payload: dict[str, object],
) -> RunEvent:
    """Append one ordered fact without committing the caller's transaction."""

    event = RunEvent(
        run_id=run_id,
        seq=await _next_run_event_seq(session, run_id),
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


_OPS_TIMELINE_EVENT_TYPES = ("tool_call", "tool_result", "model_step")


async def list_run_events_for_ops(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> list[RunEvent]:
    """List one run's tool/model timing events, ordered, for the Ops timeline.

    Excludes ``run_started``/``text_delta`` — high-volume and not useful for
    a timing/tool-audit view.
    """

    return list(
        await session.scalars(
            select(RunEvent)
            .where(
                RunEvent.run_id == run_id,
                RunEvent.event_type.in_(_OPS_TIMELINE_EVENT_TYPES),
            )
            .order_by(RunEvent.seq),
        ),
    )


async def append_text_delta(
    session: AsyncSession,
    *,
    run_id: UUID,
    delta: str,
) -> RunEvent:
    """Persist exactly the text fragment that will be emitted as an SSE event."""

    return await append_run_event(
        session,
        run_id=run_id,
        event_type="text_delta",
        payload={"delta": delta},
    )


async def append_tool_call_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    tool_name: str,
    args: dict[str, object],
) -> RunEvent:
    """Record that the model requested a tool without storing secrets."""

    return await append_run_event(
        session,
        run_id=run_id,
        event_type="tool_call",
        payload={"tool": tool_name, "args": args},
    )


async def append_tool_result_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    tool_name: str,
    provider: str | None,
    ok: bool,
    summary: str,
    duration_ms: int | None = None,
) -> RunEvent:
    """Record a short tool outcome summary for debugging and audits."""

    payload: dict[str, object] = {
        "tool": tool_name,
        "provider": provider,
        "ok": ok,
        "summary": summary[:500],
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return await append_run_event(
        session,
        run_id=run_id,
        event_type="tool_result",
        payload=payload,
    )


async def append_model_step_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    duration_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RunEvent:
    """Record one run's total wall-clock + token usage in the event timeline.

    Lightweight structured tracing (JSONB payload on the existing append-only
    run_events table) rather than a full OpenTelemetry pipeline — see
    docs/16 for why: single-machine, single-process, no collector to run.
    Coarse (whole run, not per internal model request — see
    api/chat.py::persist_model_step_event for the caller-side caveat).
    """

    return await append_run_event(
        session,
        run_id=run_id,
        event_type="model_step",
        payload={
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )


async def complete_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    assistant_content: str,
    model_messages: list[dict[str, object]] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model_request_count: int | None = None,
) -> Message:
    """Persist the final assistant message and terminal Run state together."""

    run = await _lock_run(session, run_id)
    if run.status != "running":
        raise InvalidRunStateError(f"Run {run_id} is {run.status}, not running")

    thread = await session.scalar(
        select(Thread).where(Thread.id == run.thread_id).with_for_update()
    )
    if thread is None:
        raise ThreadNotFoundError(f"Thread {run.thread_id} does not exist")
    assistant_message = Message(
        thread_id=thread.id,
        seq=await _next_message_seq(session, thread.id),
        role="assistant",
        content=assistant_content,
    )
    if model_messages is not None:
        await upsert_run_message_history(
            session,
            run_id=run.id,
            messages=model_messages,
        )

    completed_at = datetime.now(UTC)

    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.model_request_count = model_request_count

    run.status = "completed"
    run.completed_at = completed_at
    thread.updated_at = completed_at
    session.add(assistant_message)

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_completed",
        payload={"status": "completed"},
    )

    return assistant_message


async def fail_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    error_message: str = "Agent model failed.",
) -> None:
    """Mark a running execution as failed without storing internal exception details."""

    run = await _lock_run(session, run_id)
    if run.status != "running":
        return

    completed_at = datetime.now(UTC)
    run.status = "failed"
    run.error_message = error_message
    run.completed_at = completed_at

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_failed",
        payload={"status": "failed"},
    )


async def fail_orphaned_in_process_runs(
    session: AsyncSession,
    *,
    error_message: str = "Agent process restarted before this run finished.",
) -> int:
    """Fail Runs left `running`/`queued` after a process restart (not waiting_approval).

    In-process model tasks die with the API process; DB rows would otherwise block
    the thread forever via the one-active-run constraint.
    """

    orphaned = list(
        (
            await session.scalars(
                select(Run).where(Run.status.in_(("running", "queued"))).with_for_update(),
            )
        ).all(),
    )
    if not orphaned:
        return 0

    completed_at = datetime.now(UTC)
    for run in orphaned:
        run.status = "failed"
        run.error_message = error_message
        run.completed_at = completed_at
        await append_run_event(
            session,
            run_id=run.id,
            event_type="run_failed",
            payload={"status": "failed", "reason": "orphaned_on_startup"},
        )
    await session.flush()
    return len(orphaned)


async def cancel_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> None:
    """Persist cancellation so an unfinished Run is never left running or waiting."""

    run = await _lock_run(session, run_id)
    if run.status not in {"running", "waiting_approval"}:
        return

    if run.status == "waiting_approval":
        await cancel_pending_interrupts(session, run_id=run.id)

    run.status = "cancelled"
    run.completed_at = datetime.now(UTC)

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_cancelled",
        payload={"status": "cancelled"},
    )


async def upsert_run_message_history(
    session: AsyncSession,
    *,
    run_id: UUID,
    messages: list[dict[str, object]],
) -> None:
    """Insert or replace the model-message checkpoint for one Run."""

    statement = pg_insert(RunMessageHistory).values(
        run_id=run_id,
        messages=messages,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[RunMessageHistory.run_id],
        set_={"messages": messages},
    )
    await session.execute(statement)


async def get_run_message_history(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> list[dict[str, object]] | None:
    """Return the checkpoint snapshot for a Run, if any."""

    row = await session.get(RunMessageHistory, run_id)
    if row is None:
        return None
    return list(row.messages)


async def list_pending_interrupts(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> list[Interrupt]:
    """List unresolved interrupts for one Run, oldest first."""

    rows = await session.scalars(
        select(Interrupt)
        .where(
            Interrupt.run_id == run_id,
            Interrupt.status == "pending",
        )
        .order_by(Interrupt.created_at, Interrupt.tool_call_id),
    )
    return list(rows.all())


async def pause_run_for_approval(
    session: AsyncSession,
    *,
    run_id: UUID,
    approvals: list[ApprovalRequest],
    model_messages: list[dict[str, object]],
    expires_at: datetime,
) -> list[Interrupt]:
    """Freeze a running Run for HITL and persist pending interrupts + checkpoint."""

    if not approvals:
        raise InterruptDecisionError("pause_run_for_approval requires at least one approval")

    run = await _lock_run(session, run_id)
    if run.status != "running":
        raise InvalidRunStateError(f"Run {run_id} is {run.status}, not running")

    await upsert_run_message_history(
        session,
        run_id=run.id,
        messages=model_messages,
    )

    interrupts: list[Interrupt] = []
    for approval in approvals:
        interrupt = Interrupt(
            id=uuid4(),
            run_id=run.id,
            tool_call_id=approval.tool_call_id,
            tool_name=approval.tool_name,
            tool_args=approval.tool_args,
            status="pending",
            expires_at=expires_at,
        )
        session.add(interrupt)
        interrupts.append(interrupt)

    run.status = "waiting_approval"
    await session.flush()

    await append_run_event(
        session,
        run_id=run.id,
        event_type="approval_required",
        payload={
            "interrupts": [
                {
                    "id": str(item.id),
                    "tool_call_id": item.tool_call_id,
                    "tool_name": item.tool_name,
                    "tool_args": item.tool_args,
                    "expires_at": item.expires_at.isoformat(),
                }
                for item in interrupts
            ],
        },
    )
    return interrupts


async def apply_interrupt_decisions(
    session: AsyncSession,
    *,
    run_id: UUID,
    decisions: list[InterruptDecision],
    idempotency_key: str,
) -> list[Interrupt]:
    """Resolve every pending interrupt for a waiting Run and mark it running again.

    Same idempotency_key replays return the previously resolved rows without changing
    state. A different key after resolution raises InvalidRunStateError.
    """

    key = idempotency_key.strip()
    if not key:
        raise InterruptDecisionError("idempotency_key is required")

    run = await _lock_run(session, run_id)

    existing_resolved = list(
        (
            await session.scalars(
                select(Interrupt).where(
                    Interrupt.run_id == run_id,
                    Interrupt.status.in_(("approved", "denied", "timed_out")),
                    Interrupt.idempotency_key == key,
                ),
            )
        ).all(),
    )
    if existing_resolved:
        return list(
            (
                await session.scalars(
                    select(Interrupt)
                    .where(
                        Interrupt.run_id == run_id,
                        Interrupt.idempotency_key == key,
                    )
                    .order_by(Interrupt.created_at, Interrupt.tool_call_id),
                )
            ).all(),
        )

    if run.status != "waiting_approval":
        raise InvalidRunStateError(f"Run {run_id} is {run.status}, not waiting_approval")

    pending = await list_pending_interrupts(session, run_id=run_id)
    if not pending:
        raise InvalidRunStateError(f"Run {run_id} has no pending interrupts")

    pending_ids = {item.tool_call_id for item in pending}
    decision_ids = {item.tool_call_id for item in decisions}
    if pending_ids != decision_ids:
        raise InterruptDecisionError(
            "decisions must cover exactly the pending tool_call_id set",
        )

    by_id = {item.tool_call_id: item for item in pending}
    resolved_at = datetime.now(UTC)
    resolved: list[Interrupt] = []
    for decision in decisions:
        interrupt = by_id[decision.tool_call_id]
        if decision.decision == "approve":
            interrupt.status = "approved"
            # Merge form values into tool_args so resume can ToolApproved(override_args=...).
            if decision.override_args:
                merged = dict(interrupt.tool_args or {})
                merged.update(decision.override_args)
                interrupt.tool_args = merged
        else:
            interrupt.status = "denied"
            interrupt.decision_message = decision.message
        interrupt.idempotency_key = key
        interrupt.resolved_at = resolved_at
        resolved.append(interrupt)

    run.status = "running"
    await session.flush()

    await append_run_event(
        session,
        run_id=run.id,
        event_type="approval_resolved",
        payload={
            "idempotency_key": key,
            "decisions": [
                {
                    "tool_call_id": item.tool_call_id,
                    "status": item.status,
                    "message": item.decision_message,
                }
                for item in resolved
            ],
        },
    )
    return resolved


async def cancel_pending_interrupts(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> None:
    """Mark all pending interrupts cancelled (used when the waiting Run is stopped)."""

    pending = await list_pending_interrupts(session, run_id=run_id)
    resolved_at = datetime.now(UTC)
    for interrupt in pending:
        interrupt.status = "cancelled"
        interrupt.resolved_at = resolved_at
    await session.flush()


async def mark_interrupts_timed_out(
    session: AsyncSession,
    *,
    run_id: UUID,
    idempotency_key: str,
) -> list[Interrupt]:
    """Expire all pending interrupts (status timed_out) and mark the Run running for resume."""

    key = idempotency_key.strip()
    if not key:
        raise InterruptDecisionError("idempotency_key is required")

    run = await _lock_run(session, run_id)

    existing = list(
        (
            await session.scalars(
                select(Interrupt).where(
                    Interrupt.run_id == run_id,
                    Interrupt.status == "timed_out",
                    Interrupt.idempotency_key == key,
                ),
            )
        ).all(),
    )
    if existing:
        return existing

    if run.status != "waiting_approval":
        raise InvalidRunStateError(f"Run {run_id} is {run.status}, not waiting_approval")

    pending = await list_pending_interrupts(session, run_id=run_id)
    if not pending:
        raise InvalidRunStateError(f"Run {run_id} has no pending interrupts")

    resolved_at = datetime.now(UTC)
    for interrupt in pending:
        interrupt.status = "timed_out"
        interrupt.decision_message = "Approval timed out."
        interrupt.idempotency_key = key
        interrupt.resolved_at = resolved_at

    run.status = "running"
    await session.flush()

    await append_run_event(
        session,
        run_id=run.id,
        event_type="approval_resolved",
        payload={
            "idempotency_key": key,
            "decisions": [
                {
                    "tool_call_id": item.tool_call_id,
                    "status": "timed_out",
                    "message": "Approval timed out.",
                }
                for item in pending
            ],
        },
    )
    return pending
