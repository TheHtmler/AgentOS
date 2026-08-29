"""Calendar scheduling and durable execution for user-created AgentOS tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ag_ui.core import RunAgentInput
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api.chat import persist_failed_run
from agent_api.db.chat_store import (
    StartedRun,
    ThreadBusyError,
    ThreadNotFoundError,
    start_run,
)
from agent_api.db.models import Run, ScheduledTask
from agent_api.db.session import session_factory
from agent_api.runtime import AgentRuntime
from agent_api.runtime_context import ScheduledTaskExecutionContext

logger = logging.getLogger(__name__)

SCHEDULE_TYPES = frozenset({"once", "daily", "weekly", "monthly"})
_MANUAL_RUN_GRACE_SECONDS = 60


class ScheduleValidationError(ValueError):
    """Raised when a calendar schedule cannot be interpreted safely."""


def _zone(timezone_name: str) -> ZoneInfo:
    name = timezone_name.strip()
    if not name:
        raise ScheduleValidationError("timezone is required")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ScheduleValidationError(f"timezone is not a valid IANA zone: {name}") from error


def _parse_local_datetime(raw: object, timezone_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ScheduleValidationError("run_at must be an ISO local date-time")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ScheduleValidationError("run_at must be an ISO local date-time") from error
    zone = _zone(timezone_name)
    if parsed.tzinfo is not None:
        return parsed.astimezone(zone)
    return parsed.replace(tzinfo=zone)


def _parse_time(raw: object) -> time:
    if not isinstance(raw, str):
        raise ScheduleValidationError("time_of_day must use HH:MM")
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as error:
        raise ScheduleValidationError("time_of_day must use HH:MM") from error
    if parsed.second or parsed.microsecond:
        raise ScheduleValidationError("time_of_day must use HH:MM")
    return parsed


def normalize_schedule_config(
    schedule_type: str,
    *,
    run_at: datetime | None = None,
    time_of_day: str | None = None,
    days_of_week: list[int] | None = None,
    day_of_month: int | None = None,
    monthly_mode: str = "day_of_month",
    month_end_policy: str = "skip",
    timezone_name: str = "UTC",
) -> dict[str, object]:
    """Validate and normalize the user-facing schedule form into stable JSON."""

    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleValidationError("schedule_type must be once, daily, weekly, or monthly")

    if schedule_type == "once":
        if run_at is None:
            raise ScheduleValidationError("run_at is required for a one-time task")
        if run_at.tzinfo is not None:
            run_at = run_at.astimezone(_zone(timezone_name)).replace(tzinfo=None)
        return {"run_at": run_at.isoformat(timespec="minutes")}

    if time_of_day is None:
        raise ScheduleValidationError("time_of_day is required for a recurring task")
    _parse_time(time_of_day)

    if schedule_type == "daily":
        return {"time_of_day": time_of_day}

    if schedule_type == "weekly":
        if days_of_week is None or not days_of_week:
            raise ScheduleValidationError("days_of_week must contain at least one day")
        normalized_days = sorted(set(days_of_week))
        if any(day < 0 or day > 6 for day in normalized_days):
            raise ScheduleValidationError("days_of_week must contain values from 0 to 6")
        return {"time_of_day": time_of_day, "days_of_week": normalized_days}

    if monthly_mode == "last_day":
        return {"time_of_day": time_of_day, "monthly_mode": "last_day"}
    if monthly_mode != "day_of_month":
        raise ScheduleValidationError("monthly_mode must be day_of_month or last_day")
    if month_end_policy not in {"skip", "last_day"}:
        raise ScheduleValidationError("month_end_policy must be skip or last_day")
    if day_of_month is None or not 1 <= day_of_month <= 31:
        raise ScheduleValidationError("day_of_month must be between 1 and 31")
    return {
        "time_of_day": time_of_day,
        "day_of_month": day_of_month,
        "monthly_mode": "day_of_month",
        "month_end_policy": month_end_policy,
    }


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _days_in_month(year: int, month: int) -> int:
    next_year, next_month = _next_month(year, month)
    return (date(next_year, next_month, 1) - timedelta(days=1)).day


def next_run_at(
    schedule_type: str,
    schedule_config: Mapping[str, object],
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the next UTC instant after ``now`` for a stored calendar schedule."""

    zone = _zone(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(zone)

    if schedule_type == "once":
        candidate = _parse_local_datetime(schedule_config.get("run_at"), timezone_name)
        return candidate.astimezone(UTC) if candidate > current else None

    scheduled_time = _parse_time(schedule_config.get("time_of_day"))
    if schedule_type == "daily":
        candidate = datetime.combine(current.date(), scheduled_time, tzinfo=zone)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if schedule_type == "weekly":
        raw_days = schedule_config.get("days_of_week")
        if not isinstance(raw_days, list) or not raw_days:
            raise ScheduleValidationError("stored weekly schedule has no days_of_week")
        typed_days = cast(list[object], raw_days)
        days = {
            value for value in typed_days if isinstance(value, int) and not isinstance(value, bool)
        }
        if not days or any(day < 0 or day > 6 for day in days):
            raise ScheduleValidationError("stored weekly schedule has invalid days_of_week")
        for offset in range(8):
            candidate_date = current.date() + timedelta(days=offset)
            if candidate_date.weekday() not in days:
                continue
            candidate = datetime.combine(candidate_date, scheduled_time, tzinfo=zone)
            if candidate > current:
                return candidate.astimezone(UTC)
        raise ScheduleValidationError("weekly schedule has no future occurrence")

    if schedule_type == "monthly":
        raw_mode = schedule_config.get("monthly_mode", "day_of_month")
        if raw_mode == "last_day":
            year, month = current.year, current.month
            for _ in range(24):
                candidate = datetime.combine(
                    date(year, month, _days_in_month(year, month)),
                    scheduled_time,
                    tzinfo=zone,
                )
                if candidate > current:
                    return candidate.astimezone(UTC)
                year, month = _next_month(year, month)
            raise ScheduleValidationError("monthly schedule has no future occurrence")
        if raw_mode != "day_of_month":
            raise ScheduleValidationError("stored monthly schedule has invalid monthly_mode")
        raw_day = schedule_config.get("day_of_month")
        if not isinstance(raw_day, int) or isinstance(raw_day, bool) or not 1 <= raw_day <= 31:
            raise ScheduleValidationError("stored monthly schedule has invalid day_of_month")
        year, month = current.year, current.month
        raw_policy = schedule_config.get("month_end_policy", "skip")
        if raw_policy not in {"skip", "last_day"}:
            raise ScheduleValidationError("stored monthly schedule has invalid month_end_policy")
        for _ in range(24):
            days_in_month = _days_in_month(year, month)
            if raw_day <= days_in_month or raw_policy == "last_day":
                candidate = datetime.combine(
                    date(year, month, min(raw_day, days_in_month)),
                    scheduled_time,
                    tzinfo=zone,
                )
                if candidate > current:
                    return candidate.astimezone(UTC)
            year, month = _next_month(year, month)
        raise ScheduleValidationError("monthly schedule has no future occurrence")

    raise ScheduleValidationError("unknown schedule type")


@dataclass(frozen=True)
class ClaimedScheduledTask:
    """The durable Run created while claiming one scheduler occurrence."""

    task_id: UUID
    user_id: UUID
    thread_id: UUID
    run_id: UUID
    scheduled_for: datetime
    prompt: str
    execution_context: ScheduledTaskExecutionContext


def _execution_context(
    task: ScheduledTask,
    *,
    scheduled_for: datetime,
) -> ScheduledTaskExecutionContext:
    return ScheduledTaskExecutionContext(
        task_id=task.id,
        title=task.title,
        schedule_type=task.schedule_type,
        timezone=task.timezone,
        scheduled_for=scheduled_for,
        previous_run_at=task.last_run_at,
        previous_run_status=task.last_run_status,
    )


async def _task_for_update(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID | None = None,
) -> ScheduledTask | None:
    statement = select(ScheduledTask).where(
        ScheduledTask.id == task_id,
        ScheduledTask.deleted_at.is_(None),
    )
    if user_id is not None:
        statement = statement.where(ScheduledTask.owner_user_id == user_id)
    return await session.scalar(statement.with_for_update())


async def claim_due_task(
    session: AsyncSession,
    *,
    now: datetime,
) -> ClaimedScheduledTask | None:
    """Atomically advance one due task and create its ordinary AgentOS Run."""

    task = await session.scalar(
        select(ScheduledTask)
        .where(
            ScheduledTask.status == "active",
            ScheduledTask.deleted_at.is_(None),
            ScheduledTask.next_run_at.is_not(None),
            ScheduledTask.next_run_at <= now,
        )
        .order_by(ScheduledTask.next_run_at, ScheduledTask.id)
        .with_for_update(skip_locked=True)
        .limit(1),
    )
    if task is None:
        return None

    scheduled_for = task.next_run_at
    if scheduled_for is None:
        return None

    execution_context = _execution_context(task, scheduled_for=scheduled_for)

    # Advance before model work so a slow run cannot be claimed twice. A process
    # restart leaves the ordinary Run recoverable by the existing orphan sweep.
    if task.schedule_type == "once":
        task.status = "completed"
        task.next_run_at = None
    else:
        try:
            task.next_run_at = next_run_at(
                task.schedule_type,
                task.schedule_config,
                task.timezone,
                now=now,
            )
        except ScheduleValidationError as error:
            task.status = "paused"
            task.last_error = f"定时任务规则无效，请编辑后恢复：{error}"
            return None

    if task.thread_id is None:
        task.status = "paused"
        task.last_error = "定时任务没有可用的会话，请编辑任务后恢复。"
        return None

    try:
        started = await start_run(
            session,
            thread_id=task.thread_id,
            user_content=task.prompt,
            model_name="scheduled",
            user_id=task.owner_user_id,
            scheduled_task_id=task.id,
            scheduled_for=scheduled_for,
        )
    except ThreadBusyError:
        # Never overlap a task's conversation. Keep one-time tasks active so a
        # user turn already in progress does not permanently lose the reminder.
        task.status = "active"
        task.next_run_at = now + timedelta(seconds=_MANUAL_RUN_GRACE_SECONDS)
        return None
    except ThreadNotFoundError:
        task.status = "paused"
        task.last_error = "定时任务关联的会话已删除，请新建任务。"
        return None

    return ClaimedScheduledTask(
        task_id=task.id,
        user_id=task.owner_user_id,
        thread_id=started.thread_id,
        run_id=started.run_id,
        scheduled_for=scheduled_for,
        prompt=task.prompt,
        execution_context=execution_context,
    )


async def start_manual_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID,
    now: datetime,
) -> ClaimedScheduledTask:
    """Create an immediate Run without changing the task's future schedule."""

    task = await _task_for_update(session, task_id=task_id, user_id=user_id)
    if task is None or task.thread_id is None:
        raise LookupError(f"scheduled task {task_id} does not exist")
    execution_context = _execution_context(task, scheduled_for=now)
    try:
        started = await start_run(
            session,
            thread_id=task.thread_id,
            user_content=task.prompt,
            model_name="scheduled",
            user_id=user_id,
            scheduled_task_id=task.id,
            scheduled_for=now,
        )
    except (ThreadBusyError, ThreadNotFoundError) as error:
        raise error
    return ClaimedScheduledTask(
        task_id=task.id,
        user_id=user_id,
        thread_id=started.thread_id,
        run_id=started.run_id,
        scheduled_for=now,
        prompt=task.prompt,
        execution_context=execution_context,
    )


class _ScheduledRequest:
    """Small internal Request surface used to consume the existing AG-UI stream."""

    def __init__(
        self,
        app: FastAPI,
        started: StartedRun,
        body: bytes,
        scheduled_task_context: ScheduledTaskExecutionContext,
    ) -> None:
        self.app = app
        self.state = SimpleNamespace(
            prestarted_run=started,
            scheduled_task_context=scheduled_task_context,
        )
        self.headers = {"accept": "text/event-stream"}
        self._body = body

    async def body(self) -> bytes:
        return self._body

    async def is_disconnected(self) -> bool:
        return False


async def execute_claimed_task_with_user(
    app: FastAPI,
    runtime: AgentRuntime,
    claim: ClaimedScheduledTask,
) -> None:
    """Resolve the owner and consume the stream; separated for strict DB ownership."""

    from agent_api.api.ag_ui import stream_ag_ui_run
    from agent_api.db.models import User

    async with session_factory() as session:
        user = await session.get(User, claim.user_id)
    if user is None or user.status != "active":
        await persist_failed_run(
            claim.run_id,
            error_message="任务所有者已停用，无法执行定时任务。",
        )
        await finalize_task_run(claim)
        return

    input_payload = RunAgentInput.model_validate(
        {
            "threadId": str(claim.thread_id),
            "runId": str(claim.run_id),
            "state": {},
            "messages": [
                {
                    "id": f"scheduled-user-{claim.run_id}",
                    "role": "user",
                    "content": claim.prompt,
                },
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )
    request = _ScheduledRequest(
        app,
        StartedRun(thread_id=claim.thread_id, run_id=claim.run_id),
        input_payload.model_dump_json(by_alias=True).encode(),
        claim.execution_context,
    )
    try:
        response = await stream_ag_ui_run(cast(Request, request), user)
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            raise RuntimeError("scheduled AG-UI execution did not return a stream")
        async for _ in body_iterator:
            pass
    except Exception:
        logger.exception("scheduled task run failed before stream completion: %s", claim.run_id)
    finally:
        await finalize_task_run(claim)


async def finalize_task_run(claim: ClaimedScheduledTask) -> None:
    """Project the ordinary Run terminal state onto the task list summary."""

    async with session_factory() as session, session.begin():
        run = await session.get(Run, claim.run_id)
        task = await session.get(ScheduledTask, claim.task_id, with_for_update=True)
        if run is None or task is None:
            return
        task.last_run_at = run.completed_at or datetime.now(UTC)
        task.last_run_status = run.status
        task.last_error = run.error_message
        if run.status == "completed":
            task.consecutive_failures = 0
        elif run.status == "failed":
            task.consecutive_failures += 1


class ScheduledTaskScheduler:
    """Single-process dispatcher backed by PostgreSQL row locks."""

    def __init__(self, app: FastAPI, runtime: AgentRuntime, poll_seconds: float = 15.0) -> None:
        self.app = app
        self.runtime = runtime
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()
        self.max_inflight = 8

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if len(self._inflight) >= self.max_inflight:
                    await self._wait_for_next_poll()
                    continue
                claim = await self._claim_one()
                if claim is None:
                    await self._wait_for_next_poll()
                    continue
                self.dispatch(claim)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduled task dispatcher iteration failed")
                await self._wait_for_next_poll()

    async def _claim_one(self) -> ClaimedScheduledTask | None:
        async with session_factory() as session, session.begin():
            return await claim_due_task(session, now=datetime.now(UTC))

    async def _wait_for_next_poll(self) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)

    def _forget(self, task: asyncio.Task[None]) -> None:
        self._inflight.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "scheduled task execution crashed: %s",
                error,
                exc_info=(type(error), error, error.__traceback__),
            )

    def dispatch(self, claim: ClaimedScheduledTask) -> None:
        """Start one execution and retain it until its task projection is updated."""

        task = asyncio.create_task(
            execute_claimed_task_with_user(self.app, self.runtime, claim),
            name=f"scheduled-task-{claim.task_id}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._forget)

    async def stop(self) -> None:
        self._stop.set()
        tasks = list(self._inflight)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
