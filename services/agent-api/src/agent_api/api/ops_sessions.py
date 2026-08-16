"""Ops session audit: read-only thread / run / message inspection."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, String, cast, func, or_, select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, Message, Run, Thread, User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/sessions", tags=["ops-sessions"])

RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
]

_MESSAGE_PREVIEW_LIMIT = 2000
_DETAIL_MESSAGE_LIMIT = 40
_DETAIL_RUN_LIMIT = 20


class OpsThreadListItem(BaseModel):
    id: UUID
    title: str | None
    user_email: str | None
    user_status: str | None
    agent_id: UUID
    agent_slug: str
    agent_name: str
    case_id: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    last_run_status: str | None
    last_run_at: datetime | None
    message_count: int


class OpsThreadListResponse(BaseModel):
    threads: list[OpsThreadListItem]
    total: int


class OpsMessageOut(BaseModel):
    id: UUID
    seq: int
    role: str
    content: str
    created_at: datetime
    truncated: bool


class OpsRunOut(BaseModel):
    id: UUID
    status: str
    model_name: str
    error_message: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class OpsThreadDetailOut(OpsThreadListItem):
    messages: list[OpsMessageOut]
    runs: list[OpsRunOut]


def _safe_like(raw: str) -> str:
    return raw.replace("\\", "").replace("%", "").replace("_", "").strip()


def _preview(text: str) -> tuple[str, bool]:
    if len(text) <= _MESSAGE_PREVIEW_LIMIT:
        return text, False
    return text[:_MESSAGE_PREVIEW_LIMIT], True


def _list_item(
    thread: Thread,
    *,
    user_email: str | None,
    user_status: str | None,
    agent_slug: str,
    agent_name: str,
    last_run_status: str | None,
    last_run_at: datetime | None,
    message_count: int,
) -> OpsThreadListItem:
    return OpsThreadListItem(
        id=thread.id,
        title=thread.title,
        user_email=user_email,
        user_status=user_status,
        agent_id=thread.agent_id,
        agent_slug=agent_slug,
        agent_name=agent_name,
        case_id=thread.case_id,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        deleted_at=thread.deleted_at,
        last_run_status=last_run_status,
        last_run_at=last_run_at,
        message_count=message_count,
    )


def _thread_query(
    *,
    q: str | None,
    run_status: str | None,
    include_deleted: bool,
):
    last_run_id = (
        select(Run.id)
        .where(Run.thread_id == Thread.id)
        .order_by(Run.created_at.desc())
        .limit(1)
        .correlate(Thread)
        .scalar_subquery()
    )
    message_count = (
        select(func.count())
        .select_from(Message)
        .where(Message.thread_id == Thread.id)
        .correlate(Thread)
        .scalar_subquery()
    )
    stmt = (
        select(Thread, User, Agent, Run, message_count)
        .outerjoin(User, User.id == Thread.user_id)
        .join(Agent, Agent.id == Thread.agent_id)
        .outerjoin(Run, Run.id == last_run_id)
    )
    if not include_deleted:
        stmt = stmt.where(Thread.deleted_at.is_(None))
    if run_status:
        stmt = stmt.where(Run.status == run_status)
    if q:
        needle = _safe_like(q)
        clauses: list[ColumnElement[bool]] = []
        if needle:
            clauses.extend(
                (
                    Thread.title.ilike(f"%{needle}%"),
                    User.email.ilike(f"%{needle}%"),
                    Agent.slug.ilike(f"%{needle}%"),
                    Agent.name.ilike(f"%{needle}%"),
                    cast(Thread.id, String).ilike(f"{needle}%"),
                ),
            )
        with suppress(ValueError):
            clauses.append(Thread.id == UUID(q.strip()))
        if clauses:
            stmt = stmt.where(or_(*clauses))
    return stmt


@router.get("", response_model=OpsThreadListResponse)
async def list_ops_sessions(
    _subject: Annotated[str, Depends(get_ops_subject)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    run_status: Annotated[RunStatus | None, Query()] = None,
    include_deleted: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpsThreadListResponse:
    stmt = _thread_query(q=q, run_status=run_status, include_deleted=include_deleted)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    page_stmt = stmt.order_by(Thread.updated_at.desc()).limit(limit).offset(offset)

    async with session_factory() as session:
        total = int(await session.scalar(count_stmt) or 0)
        rows = (await session.execute(page_stmt)).all()

    return OpsThreadListResponse(
        total=total,
        threads=[
            _list_item(
                thread,
                user_email=None if user is None else user.email,
                user_status=None if user is None else user.status,
                agent_slug=agent.slug,
                agent_name=agent.name,
                last_run_status=None if run is None else run.status,
                last_run_at=None if run is None else (run.completed_at or run.created_at),
                message_count=int(message_count or 0),
            )
            for thread, user, agent, run, message_count in rows
        ],
    )


@router.get("/{thread_id}", response_model=OpsThreadDetailOut)
async def get_ops_session(
    thread_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsThreadDetailOut:
    last_run_id = (
        select(Run.id)
        .where(Run.thread_id == Thread.id)
        .order_by(Run.created_at.desc())
        .limit(1)
        .correlate(Thread)
        .scalar_subquery()
    )
    message_count = (
        select(func.count())
        .select_from(Message)
        .where(Message.thread_id == Thread.id)
        .correlate(Thread)
        .scalar_subquery()
    )
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Thread, User, Agent, Run, message_count)
                .outerjoin(User, User.id == Thread.user_id)
                .join(Agent, Agent.id == Thread.agent_id)
                .outerjoin(Run, Run.id == last_run_id)
                .where(Thread.id == thread_id),
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

        thread, user, agent, last_run, count = row
        messages = list(
            await session.scalars(
                select(Message)
                .where(Message.thread_id == thread_id)
                .order_by(Message.seq.desc())
                .limit(_DETAIL_MESSAGE_LIMIT),
            ),
        )
        runs = list(
            await session.scalars(
                select(Run)
                .where(Run.thread_id == thread_id)
                .order_by(Run.created_at.desc())
                .limit(_DETAIL_RUN_LIMIT),
            ),
        )

    messages.reverse()
    previews: list[OpsMessageOut] = []
    for message in messages:
        content, truncated = _preview(message.content)
        previews.append(
            OpsMessageOut(
                id=message.id,
                seq=message.seq,
                role=message.role,
                content=content,
                created_at=message.created_at,
                truncated=truncated,
            ),
        )

    return OpsThreadDetailOut(
        **_list_item(
            thread,
            user_email=None if user is None else user.email,
            user_status=None if user is None else user.status,
            agent_slug=agent.slug,
            agent_name=agent.name,
            last_run_status=None if last_run is None else last_run.status,
            last_run_at=(
                None if last_run is None else (last_run.completed_at or last_run.created_at)
            ),
            message_count=int(count or 0),
        ).model_dump(),
        messages=previews,
        runs=[
            OpsRunOut(
                id=run.id,
                status=run.status,
                model_name=run.model_name,
                error_message=None if run.error_message is None else _preview(run.error_message)[0],
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                created_at=run.created_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
            for run in runs
        ],
    )
