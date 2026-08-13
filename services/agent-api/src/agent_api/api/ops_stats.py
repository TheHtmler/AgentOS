"""Ops dashboard aggregate stats."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, KnowledgeDocument
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


class OpsStatsResponse(BaseModel):
    knowledge: KnowledgeStats
    agents: AgentStats


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
    )
