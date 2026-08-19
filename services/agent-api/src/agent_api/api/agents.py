import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import list_active_agents_with_published_versions
from agent_api.db.models import ModelProvider, User
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
    case_enabled: bool
    # False when the agent's published provider cannot process image input;
    # the chat UI disables uploads for it. Local-provider agents are vision-capable.
    supports_vision: bool


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
        provider_cache: dict[UUID, ModelProvider | None] = {}
        for agent, version in agents_and_versions:
            if version is None:
                logger.warning(
                    "Skipping active agent without published version agent_id=%s slug=%s",
                    agent.id,
                    agent.slug,
                )
                continue
            supports_vision = True
            if version.model_provider_id is not None:
                if version.model_provider_id not in provider_cache:
                    provider_cache[version.model_provider_id] = await session.get(
                        ModelProvider,
                        version.model_provider_id,
                    )
                provider = provider_cache[version.model_provider_id]
                supports_vision = bool(provider and provider.enabled and provider.supports_vision)
            responses.append(
                AgentResponse(
                    id=agent.id,
                    slug=agent.slug,
                    name=agent.name,
                    description=agent.description,
                    kind=agent.kind,
                    is_default=agent.is_default,
                    memory_enabled=version.memory_enabled,
                    case_enabled=version.case_enabled,
                    supports_vision=supports_vision,
                ),
            )

    return AgentListResponse(agents=responses)
