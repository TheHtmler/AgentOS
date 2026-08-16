"""Ops agent admin: list/patch agents and publish immutable versions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, AgentVersion
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/agents", tags=["ops-agents"])

AgentStatus = Literal["active", "disabled"]
PolicyOverride = Literal["allow", "ask", "deny"]


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


class OpsAgentVersionOut(BaseModel):
    id: UUID
    version: int
    system_prompt_overlay: str
    tool_policy_overrides: dict[str, Any] | None
    memory_enabled: bool
    case_enabled: bool
    is_published: bool
    created_at: datetime


class OpsAgentDetailOut(OpsAgentOut):
    published_version: OpsAgentVersionOut | None
    versions: list[OpsAgentVersionOut]


class OpsAgentListResponse(BaseModel):
    agents: list[OpsAgentOut]


class PatchOpsAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    status: AgentStatus | None = None
    is_default: bool | None = None


class PublishOpsAgentVersionRequest(BaseModel):
    system_prompt_overlay: str = Field(default="", max_length=20000)
    memory_enabled: bool = False
    case_enabled: bool = False
    tool_policy_overrides: dict[str, PolicyOverride] | None = None


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


def _version_out(version: AgentVersion) -> OpsAgentVersionOut:
    overrides = version.tool_policy_overrides
    return OpsAgentVersionOut(
        id=version.id,
        version=version.version,
        system_prompt_overlay=version.system_prompt_overlay,
        tool_policy_overrides=None if overrides is None else dict(overrides),
        memory_enabled=version.memory_enabled,
        case_enabled=version.case_enabled,
        is_published=version.is_published,
        created_at=version.created_at,
    )


def _detail(agent: Agent, versions: list[AgentVersion]) -> OpsAgentDetailOut:
    published = next((row for row in versions if row.is_published), None)
    base = _to_out(agent, published)
    return OpsAgentDetailOut(
        **base.model_dump(),
        published_version=None if published is None else _version_out(published),
        versions=[_version_out(row) for row in versions],
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


@router.get("/{agent_id}", response_model=OpsAgentDetailOut)
async def get_ops_agent(
    agent_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsAgentDetailOut:
    async with session_factory() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        versions = list(
            await session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc()),
            ),
        )
    return _detail(agent, versions)


@router.post("/{agent_id}/versions", response_model=OpsAgentDetailOut)
async def publish_ops_agent_version(
    agent_id: UUID,
    payload: PublishOpsAgentVersionRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsAgentDetailOut:
    overrides: dict[str, object] | None = None
    if payload.tool_policy_overrides is not None:
        overrides = {name: action for name, action in payload.tool_policy_overrides.items()}

    async with session_factory() as session, session.begin():
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        current_max = await session.scalar(
            select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id),
        )
        next_version = int(current_max or 0) + 1
        await session.execute(
            update(AgentVersion)
            .where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.is_published.is_(True),
            )
            .values(is_published=False),
        )
        session.add(
            AgentVersion(
                agent_id=agent.id,
                version=next_version,
                system_prompt_overlay=payload.system_prompt_overlay,
                tool_policy_overrides=overrides,
                memory_enabled=payload.memory_enabled,
                case_enabled=payload.case_enabled,
                is_published=True,
            ),
        )
        await session.flush()
        versions = list(
            await session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc()),
            ),
        )
        await session.refresh(agent)
        detail = _detail(agent, versions)
    return detail
