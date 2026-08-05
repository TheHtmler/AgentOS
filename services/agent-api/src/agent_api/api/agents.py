from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import get_published_version, list_active_agents
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/agents", tags=["agents"])


class AgentResponse(BaseModel):
    """A selectable Agent and its published memory setting."""

    id: UUID
    slug: str
    name: str
    description: str | None
    kind: str
    is_default: bool
    memory_enabled: bool


class AgentListResponse(BaseModel):
    """Active Agents available to the authenticated user."""

    agents: list[AgentResponse]


@router.get("", response_model=AgentListResponse)
async def get_agents(
    user: Annotated[User, Depends(get_current_user)],
) -> AgentListResponse:
    """List active Agents with settings from their published versions."""

    del user
    async with session_factory() as session:
        agents = await list_active_agents(session)
        responses = [
            AgentResponse(
                id=agent.id,
                slug=agent.slug,
                name=agent.name,
                description=agent.description,
                kind=agent.kind,
                is_default=agent.is_default,
                memory_enabled=(await get_published_version(session, agent.id)).memory_enabled,
            )
            for agent in agents
        ]

    return AgentListResponse(agents=responses)
