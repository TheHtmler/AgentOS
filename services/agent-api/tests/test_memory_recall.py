from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.case_store import create_case
from agent_api.db.models import Agent, User, UserMemory
from agent_api.memory.extract import (
    ExtractedMemoryPayload,
    upsert_extracted_memory,
)
from agent_api.memory.recall import (
    format_memory_block,
    load_relevant_memories,
    score_memories,
    select_memories_for_message,
)


def fake_memory(
    *,
    tags: list[str],
    content: str,
    kind: str = "note",
    key: str | None = None,
    embedding: list[float] | None = None,
    updated_at: datetime | None = None,
):
    return SimpleNamespace(
        tags=tags,
        content=content,
        kind=kind,
        key=key,
        embedding=embedding,
        updated_at=updated_at or datetime.now(UTC),
    )


def test_profile_always_injected_even_for_unrelated_message() -> None:
    memories = [
        fake_memory(kind="profile", key="height_cm", tags=["身高"], content="身高 75cm"),
        fake_memory(kind="note", tags=["过敏"], content="对花生过敏"),
    ]

    selected = select_memories_for_message(
        "今天天气怎么样",
        cast(list[UserMemory], memories),
    )
    assert [memory.key for memory in selected] == ["height_cm"]
    block = format_memory_block(selected)
    assert block is not None
    assert "Profile" in block
    assert "75cm" in block


def test_growth_query_keeps_profile_and_can_rank_notes() -> None:
    memories = [
        fake_memory(kind="profile", key="height_cm", tags=["身高"], content="身高 75cm"),
        fake_memory(kind="profile", key="weight_kg", tags=["体重"], content="体重 10kg"),
        fake_memory(kind="note", tags=["过敏"], content="对花生过敏"),
    ]
    selected = select_memories_for_message(
        "帮我评估一下生长情况",
        cast(list[UserMemory], memories),
    )
    keys = {memory.key for memory in selected if memory.kind == "profile"}
    assert keys == {"height_cm", "weight_kg"}


def test_hybrid_note_recall_uses_embedding_similarity() -> None:
    memories = [
        fake_memory(
            kind="note",
            tags=["饮食"],
            content="发热时按代谢门诊应急方案加糖水",
            embedding=[1.0, 0.0, 0.0],
        ),
        fake_memory(
            kind="note",
            tags=["玩具"],
            content="喜欢积木",
            embedding=[0.0, 1.0, 0.0],
        ),
    ]
    selected = select_memories_for_message(
        "生病发烧能不能随便吃东西",
        cast(list[UserMemory], memories),
        query_embedding=[0.95, 0.05, 0.0],
    )
    assert selected[0].tags == ["饮食"]


def test_keyword_score_still_ranks_tag_hits() -> None:
    memories = [
        fake_memory(tags=["身高"], content="宝宝身高 75cm"),
        fake_memory(tags=["过敏"], content="对花生过敏"),
    ]
    ranked = score_memories("宝宝身高多少了", cast(list[UserMemory], memories))
    assert ranked[0].tags == ["身高"]


@pytest.mark.anyio
async def test_recall_isolates_user_and_agent_and_always_loads_profile(
    database_session: AsyncSession,
) -> None:
    user_a = User(email=f"memory-a-{uuid4().hex}@example.com", status="active")
    user_b = User(email=f"memory-b-{uuid4().hex}@example.com", status="active")
    database_session.add_all([user_a, user_b])
    imd = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    general = await database_session.scalar(select(Agent).where(Agent.slug == "general"))
    assert imd is not None
    assert general is not None
    await database_session.flush()

    await upsert_extracted_memory(
        database_session,
        user_id=user_a.id,
        agent_id=imd.id,
        payload=ExtractedMemoryPayload(
            profile={"height_cm": 75, "weight_kg": 10},
            notes=[{"content": "对花生过敏", "tags": ["过敏"]}],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    matching = await load_relevant_memories(
        database_session,
        user_id=user_a.id,
        agent_id=imd.id,
        message="宝宝身高多少了",
    )
    other_user = await load_relevant_memories(
        database_session,
        user_id=user_b.id,
        agent_id=imd.id,
        message="宝宝身高多少了",
    )
    other_agent = await load_relevant_memories(
        database_session,
        user_id=user_a.id,
        agent_id=general.id,
        message="宝宝身高多少了",
    )
    new_thread = await load_relevant_memories(
        database_session,
        user_id=user_a.id,
        agent_id=imd.id,
        message="今天天气怎么样",
    )

    assert any(memory.key == "height_cm" for memory in matching)
    assert "75" in (format_memory_block(matching) or "")
    assert other_user == []
    assert other_agent == []
    assert any(memory.key == "height_cm" for memory in new_thread)


@pytest.mark.anyio
async def test_case_memories_are_isolated_between_cases(
    database_session: AsyncSession,
) -> None:
    user = User(email=f"memory-case-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    imd = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert imd is not None
    await database_session.flush()

    first_case = await create_case(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        display_name="第一位患者",
        make_default=False,
    )
    second_case = await create_case(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        display_name="第二位患者",
        make_default=False,
    )
    await upsert_extracted_memory(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        case_id=first_case.id,
        payload=ExtractedMemoryPayload(
            profile={"height_cm": 75},
            notes=[{"content": "第一位患者对花生过敏", "tags": ["过敏"]}],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    await upsert_extracted_memory(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        case_id=second_case.id,
        payload=ExtractedMemoryPayload(
            profile={"height_cm": 90},
            notes=[{"content": "第二位患者对牛奶过敏", "tags": ["过敏"]}],
        ),
        source_thread_id=None,
        source_run_id=None,
    )

    first_memories = await load_relevant_memories(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        case_id=first_case.id,
        message="患者身高和过敏情况",
    )
    second_memories = await load_relevant_memories(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        case_id=second_case.id,
        message="患者身高和过敏情况",
    )
    global_memories = await load_relevant_memories(
        database_session,
        user_id=user.id,
        agent_id=imd.id,
        message="患者身高和过敏情况",
    )

    first_block = format_memory_block(first_memories) or ""
    second_block = format_memory_block(second_memories) or ""
    assert "75" in first_block
    assert "第一位患者对花生过敏" in first_block
    assert "90" not in first_block
    assert "第二位患者对牛奶过敏" not in first_block
    assert "90" in second_block
    assert "第二位患者对牛奶过敏" in second_block
    assert "75" not in second_block
    assert global_memories == []
