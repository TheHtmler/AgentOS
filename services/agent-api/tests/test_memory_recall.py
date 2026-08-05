from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, User, UserMemory
from agent_api.memory.extract import upsert_extracted_facts
from agent_api.memory.recall import (
    format_memory_block,
    load_relevant_memories,
    score_memories,
    select_memories_for_message,
)


def fake_memory(*, tags: list[str], content: str, updated_at: datetime | None = None):
    """Build the small memory surface needed by pure recall ranking tests."""

    return SimpleNamespace(
        tags=tags,
        content=content,
        updated_at=updated_at or datetime.now(UTC),
    )


def test_tag_hit_ranks_height_memory() -> None:
    """A matching tag ranks ahead of unrelated active facts."""

    memories = [
        fake_memory(tags=["身高"], content="宝宝身高 75cm"),
        fake_memory(tags=["过敏"], content="对花生过敏"),
    ]

    ranked = score_memories("宝宝身高多少了", cast(list[UserMemory], memories))

    assert ranked[0].tags == ["身高"]


def test_synonym_tag_hit_ranks_height_memory() -> None:
    """身长 / 生长 queries recall facts tagged with 身高."""

    memories = [
        fake_memory(tags=["过敏"], content="对花生过敏"),
        fake_memory(tags=["身高"], content="宝宝身高 75cm"),
    ]

    ranked = score_memories("宝宝身长多少了", cast(list[UserMemory], memories))
    assert ranked[0].tags == ["身高"]

    growth = score_memories("帮我评估一下生长情况", cast(list[UserMemory], memories))
    assert growth[0].tags == ["身高"]


def test_chinese_bigram_content_overlap_recalls_memory() -> None:
    """Content recall works when Chinese wording overlaps without an exact tag hit."""

    memory = fake_memory(tags=["睡眠"], content="宝宝晚上入睡需要安抚奶嘴")

    ranked = score_memories("晚上怎么哄宝宝入睡", cast(list[UserMemory], [memory]))

    assert ranked == [memory]


def test_unrelated_message_keyword_score_empty_but_fallback_keeps_profile() -> None:
    """Keyword miss no longer means a blank new session for vertical agents."""

    memory = fake_memory(tags=["身高"], content="宝宝身高 75cm")

    assert score_memories("今天天气怎么样", cast(list[UserMemory], [memory])) == []
    selected = select_memories_for_message(
        "今天天气怎么样",
        cast(list[UserMemory], [memory]),
    )
    assert selected == [memory]
    assert format_memory_block([]) is None


@pytest.mark.anyio
async def test_recall_isolates_user_and_agent_and_only_injects_relevant_facts(
    database_session: AsyncSession,
) -> None:
    """Recall never crosses user/Agent scopes; same-agent new threads keep facts."""

    user_a = User(email=f"memory-a-{uuid4().hex}@example.com", status="active")
    user_b = User(email=f"memory-b-{uuid4().hex}@example.com", status="active")
    database_session.add_all([user_a, user_b])
    imd = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    general = await database_session.scalar(select(Agent).where(Agent.slug == "general"))
    assert imd is not None
    assert general is not None
    await database_session.flush()

    await upsert_extracted_facts(
        database_session,
        user_id=user_a.id,
        agent_id=imd.id,
        facts=[{"content": "宝宝身高 75cm", "tags": ["身高"], "op": "upsert"}],
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
    new_thread_unrelated = await load_relevant_memories(
        database_session,
        user_id=user_a.id,
        agent_id=imd.id,
        message="今天天气怎么样",
    )

    assert matching[0].tags == ["身高"]
    assert "75cm" in (format_memory_block(matching) or "")
    assert other_user == []
    assert other_agent == []
    # Soft profile: same user×agent still sees prior anthropometrics in a new thread.
    assert new_thread_unrelated[0].tags == ["身高"]
