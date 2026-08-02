from dataclasses import dataclass
from datetime import UTC, datetime
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


async def _create_thread(session: AsyncSession) -> Thread:
    thread = Thread()
    session.add(thread)
    await session.flush()
    return thread


async def _lock_thread(session: AsyncSession, thread_id: UUID) -> Thread:
    # Locking the parent row serializes allocation of messages.seq within one thread.
    thread = await session.scalar(
        select(Thread).where(Thread.id == thread_id).with_for_update(),
    )
    if thread is None:
        raise ThreadNotFoundError(f"Thread {thread_id} does not exist")

    return thread


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
) -> Run:
    """Return one Run for server-side observability."""

    run = await session.get(Run, run_id)
    if run is None:
        raise RunNotFoundError(f"Run {run_id} does not exist")

    return run


async def list_thread_messages(
    session: AsyncSession,
    *,
    thread_id: UUID,
) -> list[Message]:
    """Return final messages in their durable conversation order."""

    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise ThreadNotFoundError(f"Thread {thread_id} does not exist")

    messages = await session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.seq),
    )
    return list(messages)


async def list_completed_run_message_histories(
    session: AsyncSession,
    *,
    thread_id: UUID,
    limit: int,
) -> list[RunMessageHistory]:
    """Return recent complete model-message blocks in chronological order."""

    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise ThreadNotFoundError(f"Thread {thread_id} does not exist")

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
) -> StartedRun:
    """Record a user message and a running execution in the caller's transaction."""

    if thread_id is None:
        thread = await _create_thread(session)
    else:
        thread = await _lock_thread(session, thread_id)
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

    thread = await _lock_thread(session, run.thread_id)
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
