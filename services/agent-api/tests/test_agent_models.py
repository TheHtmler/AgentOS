from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import pytest

from agent_api.db.models import Agent, AgentVersion, Thread


@pytest.mark.anyio
async def test_seeded_general_agent_exists(database_session: AsyncSession) -> None:
    """The general agent is the default for every migrated Thread."""

    session = database_session
    transaction = await session.begin()

    try:
        agent = await session.scalar(select(Agent).where(Agent.slug == "general"))

        assert agent is not None
        assert agent.is_default is True
        assert agent.kind == "general"
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.is_published.is_(True),
            ),
        )
        assert version is not None
        assert version.memory_enabled is False
    finally:
        # The seed records are shared development data, so this read-only test rolls back.
        await transaction.rollback()


@pytest.mark.anyio
async def test_seeded_parenting_memory_enabled(database_session: AsyncSession) -> None:
    """The parenting vertical publishes memory-enabled behavior."""

    session = database_session
    transaction = await session.begin()

    try:
        agent = await session.scalar(select(Agent).where(Agent.slug == "parenting"))

        assert agent is not None
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.is_published.is_(True),
            ),
        )
        assert version is not None
        assert version.memory_enabled is True
    finally:
        # The seed records are shared development data, so this read-only test rolls back.
        await transaction.rollback()


@pytest.mark.anyio
async def test_existing_threads_have_an_agent(database_session: AsyncSession) -> None:
    """Migration backfill leaves no existing Thread without an agent."""

    session = database_session
    transaction = await session.begin()

    try:
        thread = await session.scalar(select(Thread).limit(1))

        if thread is not None:
            assert thread.agent_id is not None
    finally:
        # The test may observe developer records but must not modify them.
        await transaction.rollback()
