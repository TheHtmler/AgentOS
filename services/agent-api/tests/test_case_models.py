import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, AgentVersion


@pytest.mark.anyio
async def test_imd_has_case_enabled(database_session: AsyncSession) -> None:
    """Published IMD agent version must enable Case archives."""

    enabled = await database_session.scalar(
        select(AgentVersion.case_enabled)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(Agent.slug == "imd", AgentVersion.is_published.is_(True)),
    )
    assert enabled is True


@pytest.mark.anyio
async def test_general_case_disabled(database_session: AsyncSession) -> None:
    """General agent stays without Case archives by default."""

    enabled = await database_session.scalar(
        select(AgentVersion.case_enabled)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(Agent.slug == "general", AgentVersion.is_published.is_(True)),
    )
    assert enabled is False
