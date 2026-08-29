"""Authenticated API for user-owned scheduled AgentOS tasks."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal, TypedDict, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import AgentNotFoundError, PublishedAgentVersionNotFoundError
from agent_api.db.case_store import CaseNotFoundError
from agent_api.db.chat_store import ThreadBusyError, ThreadNotFoundError
from agent_api.db.models import Agent, Run, ScheduledTask, Thread, User, UserChannelBinding
from agent_api.db.session import session_factory
from agent_api.scheduled_tasks import (
    ScheduledTaskScheduler,
    ScheduleValidationError,
    execute_claimed_task_with_user,
    next_run_at,
    normalize_schedule_config,
    start_manual_task,
)

router = APIRouter(prefix="/v1/scheduled-tasks", tags=["scheduled-tasks"])

ScheduleType = Literal["once", "daily", "weekly", "monthly"]
MonthlyMode = Literal["day_of_month", "last_day"]
MonthEndPolicy = Literal["skip", "last_day"]


class ScheduleFields(BaseModel):
    """Shared user-facing calendar fields; all times are local to ``timezone``."""

    schedule_type: ScheduleType
    run_at: datetime | None = None
    time_of_day: str | None = Field(default=None, max_length=5)
    days_of_week: list[int] | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    monthly_mode: MonthlyMode = "day_of_month"
    month_end_policy: MonthEndPolicy = "skip"
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def strip_timezone(cls, value: str) -> str:
        return value.strip()


class ScheduledTaskCreateRequest(ScheduleFields):
    title: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=4_000)
    agent_id: UUID | None = None
    case_id: UUID | None = None
    notification_enabled: bool = False
    notification_channel: Literal["openclaw-weixin"] | None = None
    notification_binding_id: UUID | None = None

    @field_validator("title", "prompt")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized


class ScheduledTaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str | None = Field(default=None, min_length=1, max_length=4_000)
    agent_id: UUID | None = None
    schedule_type: ScheduleType | None = None
    run_at: datetime | None = None
    time_of_day: str | None = Field(default=None, max_length=5)
    days_of_week: list[int] | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    monthly_mode: MonthlyMode | None = None
    month_end_policy: MonthEndPolicy | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    notification_enabled: bool | None = None
    notification_channel: Literal["openclaw-weixin"] | None = None
    notification_binding_id: UUID | None = None

    @field_validator("title", "prompt")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized

    @field_validator("timezone")
    @classmethod
    def strip_optional_timezone(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ScheduledTaskRunResponse(BaseModel):
    id: UUID
    run_id: UUID
    scheduled_for: datetime | None
    status: str
    model_name: str
    input_tokens: int | None
    output_tokens: int | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ScheduledTaskResponse(BaseModel):
    id: UUID
    title: str
    prompt: str
    agent_id: UUID
    agent_name: str
    case_id: UUID | None
    thread_id: UUID | None
    schedule_type: str
    run_at: str | None
    time_of_day: str | None
    days_of_week: list[int] | None
    day_of_month: int | None
    monthly_mode: MonthlyMode
    month_end_policy: MonthEndPolicy
    timezone: str
    status: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_status: str | None
    last_error: str | None
    consecutive_failures: int
    unread_results: int
    created_at: datetime
    updated_at: datetime
    runs: list[ScheduledTaskRunResponse] = Field(default_factory=lambda: [])
    notification_enabled: bool
    notification_channel: str | None
    notification_binding_id: UUID | None
    notification_binding_name: str | None
    notification_last_status: str | None
    notification_last_error_code: str | None
    notification_last_at: datetime | None


class ScheduledTaskListResponse(BaseModel):
    tasks: list[ScheduledTaskResponse]


class ScheduleConfigValues(TypedDict):
    run_at: str | None
    time_of_day: str | None
    days_of_week: list[int] | None
    day_of_month: int | None
    monthly_mode: MonthlyMode
    month_end_policy: MonthEndPolicy


def _schedule_config_values(task: ScheduledTask) -> ScheduleConfigValues:
    config = task.schedule_config
    raw_days = config.get("days_of_week")
    run_at = config.get("run_at")
    time_of_day = config.get("time_of_day")
    day_of_month = config.get("day_of_month")
    monthly_mode = config.get("monthly_mode")
    month_end_policy = config.get("month_end_policy")
    typed_days = cast(list[object], raw_days) if isinstance(raw_days, list) else []
    days_of_week = (
        [value for value in typed_days if isinstance(value, int) and not isinstance(value, bool)]
        if typed_days
        else None
    )
    return {
        "run_at": run_at if isinstance(run_at, str) else None,
        "time_of_day": (time_of_day if isinstance(time_of_day, str) else None),
        "days_of_week": days_of_week,
        "day_of_month": day_of_month if isinstance(day_of_month, int) else None,
        "monthly_mode": "last_day" if monthly_mode == "last_day" else "day_of_month",
        "month_end_policy": "last_day" if month_end_policy == "last_day" else "skip",
    }


async def _task_and_agent(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> tuple[ScheduledTask, str] | None:
    statement = (
        select(ScheduledTask, Agent.name)
        .join(Agent, Agent.id == ScheduledTask.agent_id)
        .where(
            ScheduledTask.id == task_id,
            ScheduledTask.owner_user_id == user_id,
            ScheduledTask.deleted_at.is_(None),
        )
    )
    if for_update:
        statement = statement.with_for_update(of=ScheduledTask)
    result = await session.execute(statement)
    row = result.tuples().first()
    return row if row is not None else None


async def _unread_count(session: AsyncSession, task: ScheduledTask) -> int:
    statement = select(func.count(Run.id)).where(
        Run.scheduled_task_id == task.id,
        Run.status.in_(("completed", "failed", "cancelled")),
    )
    if task.result_read_at is not None:
        statement = statement.where(
            or_(Run.completed_at.is_(None), Run.completed_at > task.result_read_at),
        )
    return int((await session.scalar(statement)) or 0)


async def _recent_runs(session: AsyncSession, task_id: UUID, limit: int = 20) -> list[Run]:
    result = await session.scalars(
        select(Run)
        .where(Run.scheduled_task_id == task_id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(limit),
    )
    return list(result.all())


def _run_response(run: Run) -> ScheduledTaskRunResponse:
    return ScheduledTaskRunResponse(
        id=run.id,
        run_id=run.id,
        scheduled_for=run.scheduled_for,
        status=run.status,
        model_name=run.model_name,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        error_message=run.error_message,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


async def _to_response(
    session: AsyncSession,
    task: ScheduledTask,
    agent_name: str,
) -> ScheduledTaskResponse:
    values = _schedule_config_values(task)
    runs: list[Run] = await _recent_runs(session, task.id)
    binding_name = None
    if task.notification_binding_id is not None:
        binding_name = await session.scalar(
            select(UserChannelBinding.display_name).where(
                UserChannelBinding.id == task.notification_binding_id
            )
        )
    return ScheduledTaskResponse(
        id=task.id,
        title=task.title,
        prompt=task.prompt,
        agent_id=task.agent_id,
        agent_name=agent_name,
        case_id=task.case_id,
        thread_id=task.thread_id,
        schedule_type=task.schedule_type,
        run_at=values["run_at"],
        time_of_day=values["time_of_day"],
        days_of_week=values["days_of_week"],
        day_of_month=values["day_of_month"],
        monthly_mode=values["monthly_mode"],
        month_end_policy=values["month_end_policy"],
        timezone=task.timezone,
        status=task.status,
        next_run_at=task.next_run_at,
        last_run_at=task.last_run_at,
        last_run_status=task.last_run_status,
        last_error=task.last_error,
        consecutive_failures=task.consecutive_failures,
        unread_results=await _unread_count(session, task),
        created_at=task.created_at,
        updated_at=task.updated_at,
        runs=[_run_response(run) for run in runs],
        notification_enabled=task.notification_enabled,
        notification_channel=task.notification_channel,
        notification_binding_id=task.notification_binding_id,
        notification_binding_name=binding_name,
        notification_last_status=task.notification_last_status,
        notification_last_error_code=task.notification_last_error_code,
        notification_last_at=task.notification_last_at,
    )


async def _resolve_notification_binding_id(
    session: AsyncSession,
    *,
    user_id: UUID,
    enabled: bool,
    channel: str | None,
    binding_id: UUID | None,
) -> UUID | None:
    if not enabled:
        return None
    if channel != "openclaw-weixin":
        raise HTTPException(status_code=422, detail="启用微信推送时必须选择有效的微信绑定")
    if binding_id is None:
        binding_id = await session.scalar(
            select(UserChannelBinding.id)
            .where(
                UserChannelBinding.user_id == user_id,
                UserChannelBinding.channel == channel,
                UserChannelBinding.status == "active",
                UserChannelBinding.receive_notifications.is_(True),
                UserChannelBinding.is_default.is_(True),
            )
            .order_by(UserChannelBinding.created_at)
            .limit(1)
        )
        if binding_id is None:
            raise HTTPException(status_code=422, detail="请先绑定可接收通知的微信账号")
    binding = await session.scalar(
        select(UserChannelBinding).where(
            UserChannelBinding.id == binding_id,
            UserChannelBinding.user_id == user_id,
            UserChannelBinding.channel == channel,
            UserChannelBinding.status == "active",
            UserChannelBinding.receive_notifications.is_(True),
        )
    )
    if binding is None:
        raise HTTPException(status_code=422, detail="微信绑定不存在或已失效")
    return binding.id


def _normalize_schedule(body: ScheduleFields) -> tuple[dict[str, object], datetime]:
    config = normalize_schedule_config(
        body.schedule_type,
        run_at=body.run_at,
        time_of_day=body.time_of_day,
        days_of_week=body.days_of_week,
        day_of_month=body.day_of_month,
        monthly_mode=body.monthly_mode,
        month_end_policy=body.month_end_policy,
        timezone_name=body.timezone,
    )
    current = datetime.now(UTC)
    next_at = next_run_at(body.schedule_type, config, body.timezone, now=current)
    if next_at is None:
        raise ScheduleValidationError("run_at must be in the future")
    return config, next_at


@router.get("", response_model=ScheduledTaskListResponse)
async def list_scheduled_tasks(
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ScheduledTaskListResponse:
    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ScheduledTask, Agent.name)
                    .join(Agent, Agent.id == ScheduledTask.agent_id)
                    .where(
                        ScheduledTask.owner_user_id == user.id,
                        ScheduledTask.deleted_at.is_(None),
                    )
                    .order_by(ScheduledTask.updated_at.desc(), ScheduledTask.id.desc())
                    .limit(limit),
                )
            ).all()
        )
        tasks = [await _to_response(session, task, agent_name) for task, agent_name in rows]
    return ScheduledTaskListResponse(tasks=tasks)


@router.post("", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_scheduled_task(
    body: ScheduledTaskCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    try:
        config, next_at = _normalize_schedule(body)
        async with session_factory() as session, session.begin():
            from agent_api.db.chat_store import create_empty_thread

            thread = await create_empty_thread(
                session,
                user_id=user.id,
                agent_id=body.agent_id,
                case_id=body.case_id,
            )
            thread.title = body.title
            task = ScheduledTask(
                owner_user_id=user.id,
                agent_id=thread.agent_id,
                case_id=thread.case_id,
                thread_id=thread.id,
                title=body.title,
                prompt=body.prompt,
                schedule_type=body.schedule_type,
                schedule_config=config,
                timezone=body.timezone,
                next_run_at=next_at,
                notification_enabled=body.notification_enabled,
                notification_channel=body.notification_channel
                if body.notification_enabled
                else None,
                notification_binding_id=body.notification_binding_id
                if body.notification_enabled
                else None,
            )
            task.notification_binding_id = await _resolve_notification_binding_id(
                session,
                user_id=user.id,
                enabled=body.notification_enabled,
                channel=task.notification_channel,
                binding_id=task.notification_binding_id,
            )
            session.add(task)
            await session.flush()
            await session.refresh(task)
            agent = await session.get(Agent, task.agent_id)
            return await _to_response(session, task, agent.name if agent is not None else "")
    except ScheduleValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AgentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    except PublishedAgentVersionNotFoundError as error:
        raise HTTPException(
            status_code=409,
            detail="Agent configuration has no published version",
        ) from error
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case not found") from error


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    async with session_factory() as session:
        row = await _task_and_agent(session, task_id=task_id, user_id=user.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        return await _to_response(session, row[0], row[1])


@router.patch("/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    task_id: UUID,
    body: ScheduledTaskUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    try:
        async with session_factory() as session, session.begin():
            row = await _task_and_agent(
                session,
                task_id=task_id,
                user_id=user.id,
                for_update=True,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Scheduled task not found")
            task, agent_name = row
            if body.title is not None:
                task.title = body.title
                if task.thread_id is not None:
                    thread = await session.get(Thread, task.thread_id)
                    if thread is not None:
                        thread.title = body.title
            if body.prompt is not None:
                task.prompt = body.prompt
            if "agent_id" in body.model_fields_set and body.agent_id != task.agent_id:
                raise HTTPException(
                    status_code=409,
                    detail="The assistant is fixed when a scheduled task is created",
                )

            schedule_fields = {
                "schedule_type",
                "run_at",
                "time_of_day",
                "days_of_week",
                "day_of_month",
                "timezone",
                "monthly_mode",
                "month_end_policy",
            }
            if body.model_fields_set & schedule_fields:
                current = _schedule_config_values(task)
                timezone_name = body.timezone or task.timezone
                schedule_type = body.schedule_type or task.schedule_type
                config = normalize_schedule_config(
                    schedule_type,
                    run_at=body.run_at
                    if "run_at" in body.model_fields_set
                    else (
                        datetime.fromisoformat(str(current["run_at"]))
                        if current["run_at"] is not None
                        else None
                    ),
                    time_of_day=(
                        body.time_of_day
                        if "time_of_day" in body.model_fields_set
                        else current["time_of_day"]
                    ),
                    days_of_week=(
                        body.days_of_week
                        if "days_of_week" in body.model_fields_set
                        else current["days_of_week"]
                    ),
                    day_of_month=(
                        body.day_of_month
                        if "day_of_month" in body.model_fields_set
                        else current["day_of_month"]
                    ),
                    monthly_mode=(
                        body.monthly_mode or current["monthly_mode"]
                        if "monthly_mode" in body.model_fields_set
                        else current["monthly_mode"]
                    ),
                    month_end_policy=(
                        body.month_end_policy or current["month_end_policy"]
                        if "month_end_policy" in body.model_fields_set
                        else current["month_end_policy"]
                    ),
                    timezone_name=timezone_name,
                )
                next_at = next_run_at(schedule_type, config, timezone_name, now=datetime.now(UTC))
                if next_at is None:
                    raise ScheduleValidationError("run_at must be in the future")
                task.schedule_type = schedule_type
                task.schedule_config = config
                task.timezone = timezone_name
                task.next_run_at = next_at
                task.status = "active"
            notification_fields = {
                "notification_enabled",
                "notification_channel",
                "notification_binding_id",
            }
            if body.model_fields_set & notification_fields:
                enabled = bool(
                    body.notification_enabled
                    if "notification_enabled" in body.model_fields_set
                    else task.notification_enabled
                )
                channel = (
                    body.notification_channel
                    if "notification_channel" in body.model_fields_set
                    else task.notification_channel
                )
                binding_id = (
                    body.notification_binding_id
                    if "notification_binding_id" in body.model_fields_set
                    else task.notification_binding_id
                )
                if not enabled:
                    channel = None
                    binding_id = None
                task.notification_binding_id = await _resolve_notification_binding_id(
                    session,
                    user_id=user.id,
                    enabled=enabled,
                    channel=channel,
                    binding_id=binding_id,
                )
                task.notification_enabled = enabled
                task.notification_channel = channel
            await session.flush()
            await session.refresh(task)
            return await _to_response(session, task, agent_name)
    except ScheduleValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


async def _set_task_status(task_id: UUID, user_id: UUID, next_status: str) -> ScheduledTaskResponse:
    async with session_factory() as session, session.begin():
        row = await _task_and_agent(session, task_id=task_id, user_id=user_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        task, agent_name = row
        if next_status == "paused":
            if task.status == "completed":
                raise HTTPException(status_code=409, detail="One-time task has already completed")
            task.status = "paused"
        else:
            if task.status == "completed" and task.schedule_type == "once":
                raise HTTPException(status_code=409, detail="One-time task has already completed")
            task.status = "active"
            task.next_run_at = next_run_at(
                task.schedule_type,
                task.schedule_config,
                task.timezone,
                now=datetime.now(UTC),
            )
        await session.flush()
        await session.refresh(task)
        return await _to_response(session, task, agent_name)


@router.post("/{task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_scheduled_task(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    return await _set_task_status(task_id, user.id, "paused")


@router.post("/{task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_scheduled_task(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    return await _set_task_status(task_id, user.id, "active")


@router.post("/{task_id}/read", response_model=ScheduledTaskResponse)
async def mark_scheduled_task_read(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskResponse:
    async with session_factory() as session, session.begin():
        row = await _task_and_agent(session, task_id=task_id, user_id=user.id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        task, agent_name = row
        task.result_read_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(task)
        return await _to_response(session, task, agent_name)


@router.post("/{task_id}/run", response_model=ScheduledTaskRunResponse, status_code=202)
async def run_scheduled_task_now(
    task_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> ScheduledTaskRunResponse:
    try:
        async with session_factory() as session, session.begin():
            claim = await start_manual_task(
                session,
                task_id=task_id,
                user_id=user.id,
                now=datetime.now(UTC),
            )
    except ThreadBusyError as error:
        raise HTTPException(status_code=409, detail="This task is already running") from error
    except ThreadNotFoundError as error:
        raise HTTPException(
            status_code=409,
            detail="Scheduled task conversation is unavailable",
        ) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Scheduled task not found") from error

    scheduler = getattr(request.app.state, "scheduled_task_scheduler", None)
    if isinstance(scheduler, ScheduledTaskScheduler):
        scheduler.dispatch(claim)
    else:
        # Tests and maintenance scripts may construct an app without lifespan.
        from agent_api.runtime import get_runtime

        asyncio.create_task(
            execute_claimed_task_with_user(request.app, get_runtime(request), claim),
            name=f"manual-scheduled-task-{task_id}",
        )
    async with session_factory() as session:
        run = await session.get(Run, claim.run_id)
        if run is None:
            raise HTTPException(status_code=500, detail="Scheduled run disappeared")
        return _run_response(run)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_task(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    async with session_factory() as session, session.begin():
        row = await _task_and_agent(session, task_id=task_id, user_id=user.id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail="Scheduled task not found")
        task, _ = row
        task.deleted_at = datetime.now(UTC)
        task.status = "paused"
        task.next_run_at = None
