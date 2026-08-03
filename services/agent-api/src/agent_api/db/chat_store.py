from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Message, Run, RunEvent, RunMessageHistory, Thread


class ThreadNotFoundError(LookupError):
    """Raised when a requested conversation does not exist."""


class ThreadBusyError(RuntimeError):
    """Raised when a Thread already has an active model execution."""


class RunNotFoundError(LookupError):
    """Raised when a requested agent execution does not exist."""


class InvalidRunStateError(ValueError):
    """Raised when a terminal update is attempted for a non-running Run."""


@dataclass(frozen=True)
class StartedRun:
    """Identifiers created together before model streaming begins."""

    thread_id: UUID
    run_id: UUID


@dataclass(frozen=True)
class ThreadListItem:
    """One recent conversation summary for the chat navigation."""

    id: UUID
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


async def _create_thread(session: AsyncSession, *, user_id: UUID) -> Thread:
    thread = Thread(user_id=user_id)
    session.add(thread)
    await session.flush()
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
    active_run_id = await session.scalar(
        select(Run.id)
        .where(
            Run.thread_id == thread_id,
            Run.status == "running",
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
) -> list[ThreadListItem]:
    """Return recent Threads with a render-safe latest-message preview."""

    latest_message_content = (
        select(Message.content)
        .where(Message.thread_id == Thread.id)
        .order_by(Message.seq.desc())
        .limit(1)
        .scalar_subquery()
    )

    result = await session.execute(
        select(Thread, latest_message_content)
        .where(
            Thread.user_id == user_id,
            Thread.deleted_at.is_(None),
        )
        .order_by(Thread.updated_at.desc(), Thread.id.desc())
        .limit(limit),
    )

    return [
        ThreadListItem(
            id=thread.id,
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
) -> StartedRun:
    """Record a user message and a running execution in the caller's transaction.

    HTTP handlers always supply ``user_id``. The optional value keeps isolated legacy repository
    tests usable while ownerless development records remain inaccessible through every API.
    """

    if thread_id is None:
        if user_id is None:
            thread = Thread()
            session.add(thread)
            await session.flush()
        else:
            thread = await _create_thread(session, user_id=user_id)
    else:
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
) -> RunEvent:
    """Record a short tool outcome summary for debugging and audits."""

    return await append_run_event(
        session,
        run_id=run_id,
        event_type="tool_result",
        payload={
            "tool": tool_name,
            "provider": provider,
            "ok": ok,
            "summary": summary[:500],
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
        session.add(
            RunMessageHistory(
                run_id=run.id,
                messages=model_messages,
            ),
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
) -> None:
    """Mark a running execution as failed without storing internal exception details."""

    run = await _lock_run(session, run_id)
    if run.status != "running":
        return

    completed_at = datetime.now(UTC)
    run.status = "failed"
    run.error_message = "Agent model failed."
    run.completed_at = completed_at

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_failed",
        payload={"status": "failed"},
    )


async def cancel_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> None:
    """Persist browser cancellation so an unfinished Run is never left running."""

    run = await _lock_run(session, run_id)
    if run.status != "running":
        return

    run.status = "cancelled"
    run.completed_at = datetime.now(UTC)

    await append_run_event(
        session,
        run_id=run.id,
        event_type="run_cancelled",
        payload={"status": "cancelled"},
    )
