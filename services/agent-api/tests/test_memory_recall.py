from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from agent_api.db.models import UserMemory
from agent_api.memory.recall import format_memory_block, score_memories


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
    """身长 queries recall facts tagged with its 身高 synonym."""

    memories = [
        fake_memory(tags=["过敏"], content="对花生过敏"),
        fake_memory(tags=["身高"], content="宝宝身高 75cm"),
    ]

    ranked = score_memories("宝宝身长多少了", cast(list[UserMemory], memories))

    assert ranked[0].tags == ["身高"]


def test_unrelated_message_returns_empty_block() -> None:
    """No recalled facts means no memory instruction section."""

    memory = fake_memory(tags=["身高"], content="宝宝身高 75cm")

    assert score_memories("今天天气怎么样", cast(list[UserMemory], [memory])) == []
    assert format_memory_block([]) is None
