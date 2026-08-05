import asyncio
import inspect
from typing import cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import Agent, User
from agent_api.memory.extract import (
    extract_measurement_facts,
    merge_fact_lists,
    schedule_memory_extract,
    upsert_extracted_facts,
)
from agent_api.thread_title import schedule_auto_thread_title


def test_extract_measurement_facts_from_chinese_message() -> None:
    facts = extract_measurement_facts("男宝，身高 86cm，体重 12.5kg，今天体检")
    primary_tags = {
        cast(list[str], fact["tags"])[0]
        for fact in facts
        if isinstance(fact.get("tags"), list)
    }
    contents = [str(fact["content"]) for fact in facts]
    assert {"身高", "体重"} <= primary_tags
    assert any("86" in content for content in contents)
    assert any("12.5" in content for content in contents)


def test_post_complete_scheduler_signatures_match_call_sites() -> None:
    """Title jobs omit memory_enabled; extract jobs require it (avoids TypeError on done)."""

    title_params = inspect.signature(schedule_auto_thread_title).parameters
    extract_params = inspect.signature(schedule_memory_extract).parameters
    assert "memory_enabled" not in title_params
    assert "memory_enabled" in extract_params
    assert extract_params["memory_enabled"].default is inspect.Parameter.empty


@pytest.mark.anyio
async def test_upsert_archives_previous_same_primary_tag(database_session: AsyncSession) -> None:
    """A newer fact replaces the active memory for its primary tag."""

    user = User(email=f"memory-test-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
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


@pytest.mark.anyio
async def test_upsert_does_not_archive_an_adjacent_secondary_tag(
    database_session: AsyncSession,
) -> None:
    """Only the primary tag defines the replacement group."""

    user = User(email=f"memory-tags-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert agent is not None
    await database_session.flush()

    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝对花生过敏", "tags": ["过敏", "饮食"]}],
        source_thread_id=None,
        source_run_id=None,
    )
    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝喜欢吃面条", "tags": ["饮食"]}],
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    active = await list_active_memories(database_session, user_id=user.id, agent_id=agent.id)

    assert {memory.content for memory in active} == {"宝宝对花生过敏", "宝宝喜欢吃面条"}


@pytest.mark.anyio
async def test_regex_facts_persist_when_model_extract_is_empty(
    database_session: AsyncSession,
) -> None:
    """Local models that skip extraction still persist explicit 身高/体重."""

    user = User(email=f"memory-regex-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert agent is not None
    await database_session.flush()

    facts = merge_fact_lists(
        extract_measurement_facts("宝宝身高 86cm，体重 12kg"),
        [],
    )
    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=facts,
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    active = await list_active_memories(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
    )
    tags = {memory.tags[0] for memory in active if memory.tags}
    assert {"身高", "体重"} <= tags


@pytest.mark.anyio
async def test_extract_schedule_does_not_call_extractor_when_memory_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A memory-disabled Agent exits before invoking extraction or writing rows."""

    extractor_called = False

    async def fake_extract(
        user_message: str,
        assistant_content: str,
        http_client: httpx.AsyncClient,
    ) -> list[dict[str, object]]:
        nonlocal extractor_called
        extractor_called = True
        return [{"content": "宝宝身高 75cm", "tags": ["身高"], "op": "upsert"}]

    schedule_memory_extract(
        user_id=uuid4(),
        agent_id=uuid4(),
        thread_id=uuid4(),
        run_id=uuid4(),
        user_message="宝宝身高多少了",
        assistant_content="上次记录是 75cm。",
        model_semaphore=asyncio.Semaphore(1),
        http_client=cast(httpx.AsyncClient, object()),
        memory_enabled=False,
        extract_facts=fake_extract,
    )
    await asyncio.sleep(0)

    assert extractor_called is False
