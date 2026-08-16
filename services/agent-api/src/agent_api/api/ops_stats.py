"""Ops dashboard aggregate stats."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, KnowledgeDocument, Run, Thread, User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops", tags=["ops"])


class KnowledgeStats(BaseModel):
    documents_total: int
    curated: int
    clinically_reviewed: int
    withdrawn: int


class AgentStats(BaseModel):
    active: int
    disabled: int


class UserStats(BaseModel):
    total: int
    active: int


class SessionStats(BaseModel):
    threads_total: int
    waiting_approval: int


class RecentThreadOut(BaseModel):
    id: UUID
    title: str | None
    user_email: str | None
    agent_name: str
    updated_at: datetime
    last_run_status: str | None


class OpsStatsResponse(BaseModel):
    knowledge: KnowledgeStats
    agents: AgentStats
    users: UserStats
    sessions: SessionStats
    recent_threads: list[RecentThreadOut]


@router.get("/stats", response_model=OpsStatsResponse)
async def get_ops_stats(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsStatsResponse:
    async with session_factory() as session:
        review_rows = (
            await session.execute(
                select(KnowledgeDocument.review_status, func.count()).group_by(
                    KnowledgeDocument.review_status
                ),
            )
        ).all()
        review_counts = {str(status): int(count) for status, count in review_rows}
        agent_rows = (
            await session.execute(select(Agent.status, func.count()).group_by(Agent.status))
        ).all()
        agent_counts = {str(status): int(count) for status, count in agent_rows}
        user_rows = (
            await session.execute(select(User.status, func.count()).group_by(User.status))
        ).all()
        user_counts = {str(status): int(count) for status, count in user_rows}
        threads_total = int(
            await session.scalar(
                select(func.count()).select_from(Thread).where(Thread.deleted_at.is_(None)),
            )
            or 0
        )
        waiting_approval = int(
            await session.scalar(
                select(func.count()).select_from(Run).where(Run.status == "waiting_approval"),
            )
            or 0
        )

        last_run_id = (
            select(Run.id)
            .where(Run.thread_id == Thread.id)
            .order_by(Run.created_at.desc())
            .limit(1)
            .correlate(Thread)
            .scalar_subquery()
        )
        recent_rows = (
            await session.execute(
                select(Thread, User.email, Agent.name, Run.status)
                .outerjoin(User, User.id == Thread.user_id)
                .join(Agent, Agent.id == Thread.agent_id)
                .outerjoin(Run, Run.id == last_run_id)
                .where(Thread.deleted_at.is_(None))
                .order_by(Thread.updated_at.desc())
                .limit(8),
            )
        ).all()

    curated = review_counts.get("curated", 0)
    clinically_reviewed = review_counts.get("clinically_reviewed", 0)
    withdrawn = review_counts.get("withdrawn", 0)
    return OpsStatsResponse(
        knowledge=KnowledgeStats(
            documents_total=curated + clinically_reviewed + withdrawn,
            curated=curated,
            clinically_reviewed=clinically_reviewed,
            withdrawn=withdrawn,
        ),
        agents=AgentStats(
            active=agent_counts.get("active", 0),
            disabled=agent_counts.get("disabled", 0),
        ),
        users=UserStats(
            total=sum(user_counts.values()),
            active=user_counts.get("active", 0),
        ),
        sessions=SessionStats(
            threads_total=threads_total,
            waiting_approval=waiting_approval,
        ),
        recent_threads=[
            RecentThreadOut(
                id=thread.id,
                title=thread.title,
                user_email=email,
                agent_name=agent_name,
                updated_at=thread.updated_at,
                last_run_status=run_status,
            )
            for thread, email, agent_name, run_status in recent_rows
        ],
    )
