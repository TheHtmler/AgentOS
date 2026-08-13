"""Ops agent admin: list all agents and patch basic fields."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select, update

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, AgentVersion
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/agents", tags=["ops-agents"])

AgentStatus = Literal["active", "disabled"]


class OpsAgentOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    kind: str
    status: str
    is_default: bool
    memory_enabled: bool | None
    case_enabled: bool | None
    updated_at: datetime


class OpsAgentListResponse(BaseModel):
    agents: list[OpsAgentOut]


class PatchOpsAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    status: AgentStatus | None = None
    is_default: bool | None = None


def _to_out(agent: Agent, version: AgentVersion | None) -> OpsAgentOut:
    return OpsAgentOut(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        kind=agent.kind,
        status=agent.status,
        is_default=agent.is_default,
        memory_enabled=None if version is None else version.memory_enabled,
        case_enabled=None if version is None else version.case_enabled,
        updated_at=agent.updated_at,
    )


@router.get("", response_model=OpsAgentListResponse)
async def list_ops_agents(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsAgentListResponse:
    async with session_factory() as session:
        rows = await session.execute(
            select(Agent, AgentVersion)
            .outerjoin(
                AgentVersion,
                and_(
                    AgentVersion.agent_id == Agent.id,
                    AgentVersion.is_published.is_(True),
                ),
            )
            .order_by(Agent.is_default.desc(), Agent.slug),
        )
        items = [_to_out(agent, version) for agent, version in rows.tuples()]
    return OpsAgentListResponse(agents=items)


@router.patch("/{agent_id}", response_model=OpsAgentOut)
async def patch_ops_agent(
    agent_id: UUID,
    payload: PatchOpsAgentRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsAgentOut:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    async with session_factory() as session, session.begin():
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        if data.get("status") == "disabled" and agent.is_default:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot disable the default agent; set another default first",
            )

        if "is_default" in data:
            if data["is_default"] is not True:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Clearing default is not supported; set another agent as default",
                )
            await session.execute(update(Agent).values(is_default=False))
            agent.is_default = True
            data.pop("is_default", None)

        if "name" in data and data["name"] is not None:
            agent.name = data["name"]
        if "description" in data:
            agent.description = data["description"]
        if "status" in data and data["status"] is not None:
            agent.status = data["status"]

        await session.flush()
        await session.refresh(agent)
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.is_published.is_(True),
            ),
        )
        out = _to_out(agent, version)
    return out
