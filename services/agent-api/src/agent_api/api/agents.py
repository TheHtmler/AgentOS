import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import list_active_agents_with_published_versions
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/agents", tags=["agents"])
logger = logging.getLogger(__name__)


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
        agents_and_versions = await list_active_agents_with_published_versions(session)
        responses: list[AgentResponse] = []
        for agent, version in agents_and_versions:
            if version is None:
                logger.warning(
                    "Skipping active agent without published version agent_id=%s slug=%s",
                    agent.id,
                    agent.slug,
                )
                continue
            responses.append(
                AgentResponse(
                    id=agent.id,
                    slug=agent.slug,
                    name=agent.name,
                    description=agent.description,
                    kind=agent.kind,
                    is_default=agent.is_default,
                    memory_enabled=version.memory_enabled,
                ),
            )

    return AgentListResponse(agents=responses)
