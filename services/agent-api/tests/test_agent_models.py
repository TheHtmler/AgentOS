import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, AgentVersion, Thread, UserMemory


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


@pytest.mark.anyio
async def test_user_memory_has_nullable_thread_and_run_provenance(
    database_session: AsyncSession,
) -> None:
    """Memory provenance may point to its source Thread and Run when available."""

    source_thread_id = UserMemory.__table__.c.source_thread_id
    source_run_id = UserMemory.__table__.c.source_run_id

    assert source_thread_id.nullable is True
    assert source_run_id.nullable is True

    columns = (
        await database_session.execute(
            text(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'user_memories'
                  AND column_name IN ('source_thread_id', 'source_run_id')
                ORDER BY column_name
                """,
            ),
        )
    ).all()

    assert columns == [
        ("source_run_id", "YES"),
        ("source_thread_id", "YES"),
    ]
