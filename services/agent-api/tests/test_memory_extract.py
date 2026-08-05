from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import Agent, User
from agent_api.memory.extract import upsert_extracted_facts


@pytest.mark.anyio
async def test_upsert_archives_previous_same_primary_tag(database_session: AsyncSession) -> None:
    """A newer fact replaces the active memory for its primary tag."""

    user = User(email=f"memory-test-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "parenting"))
    assert agent is not None
    await database_session.flush()

    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝身高 75cm", "tags": ["身高"], "op": "upsert"}],
        source_thread_id=None,
        source_run_id=None,
    )
    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝身高 78cm", "tags": ["身高"], "op": "upsert"}],
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    active = await list_active_memories(database_session, user_id=user.id, agent_id=agent.id)

    assert len([memory for memory in active if "身高" in memory.tags]) == 1
    assert "78" in active[0].content
