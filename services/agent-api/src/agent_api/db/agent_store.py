from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, AgentVersion


class AgentNotFoundError(LookupError):
    """Raised when an Agent is missing or unavailable for a new Thread."""


class PublishedAgentVersionNotFoundError(LookupError):
    """Raised when an Agent does not have a published configuration."""


async def list_active_agents(session: AsyncSession) -> list[Agent]:
    """Return selectable Agents in a stable navigation order."""

    agents = await session.scalars(
        select(Agent)
        .where(Agent.status == "active")
        .order_by(Agent.is_default.desc(), Agent.slug),
    )
    return list(agents)


async def list_active_agents_with_published_versions(
    session: AsyncSession,
) -> list[tuple[Agent, AgentVersion | None]]:
    """Return active Agents and their published revision in one query.

    An Agent without a published revision is returned with ``None`` so callers can
    skip it without turning a selectable-agent list into a server error.
    """

    rows = await session.execute(
        select(Agent, AgentVersion)
        .outerjoin(
            AgentVersion,
            and_(
                AgentVersion.agent_id == Agent.id,
                AgentVersion.is_published.is_(True),
            ),
        )
        .where(Agent.status == "active")
        .order_by(Agent.is_default.desc(), Agent.slug)
    )
    return list(rows.tuples())


async def get_default_agent_id(session: AsyncSession) -> UUID:
    """Return the active default Agent identifier."""

    agent_id = await session.scalar(
        select(Agent.id)
        .where(
            Agent.is_default.is_(True),
            Agent.status == "active",
        )
        .limit(1),
    )
    if agent_id is None:
        raise AgentNotFoundError("No active default Agent is configured")
    return agent_id


async def get_published_version(session: AsyncSession, agent_id: UUID) -> AgentVersion:
    """Return the configuration revision currently published for an Agent."""

    version = await session.scalar(
        select(AgentVersion)
        .where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_published.is_(True),
        )
        .order_by(AgentVersion.version.desc())
        .limit(1),
    )
    if version is None:
        raise PublishedAgentVersionNotFoundError(
            f"Agent {agent_id} does not have a published version",
        )
    return version


async def resolve_agent_for_new_thread(
    session: AsyncSession,
    agent_id: UUID | None,
) -> UUID:
    """Return agent_id for a new Thread; default if omitted; reject missing or disabled Agents."""

    if agent_id is None:
        return await get_default_agent_id(session)

    agent = await session.get(Agent, agent_id)
    if agent is None or agent.status != "active":
        raise AgentNotFoundError(f"Agent {agent_id} does not exist or is disabled")
    return agent.id
