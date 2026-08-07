from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.recall import (
    CASE_HEADER,
    current_facts_by_key,
    format_case_block,
    history_excluding_current,
    load_case_block,
)
from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User
from agent_api.memory.recall import format_memory_block
from agent_api.db.models import UserMemory

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_format_case_block_empty() -> None:
    assert format_case_block([]) is None


def test_format_case_block_current_and_history_with_timestamps() -> None:
    case_id = uuid4()
    newer = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="height_cm",
        content="身高 82.5 cm",
        tags=["身高"],
        status="confirmed",
        updated_at=datetime(2026, 8, 6, 7, 20, tzinfo=UTC),
        created_at=datetime(2026, 8, 6, 7, 20, tzinfo=UTC),
    )
    older = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="height_cm",
        content="身高 82 cm",
        tags=["身高"],
        status="archived",
        updated_at=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
        created_at=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
    )
    block = format_case_block([newer], history=[newer, older])
    assert block is not None
    assert block.startswith(CASE_HEADER)
    assert "### Current" in block
    assert "### History" in block
    assert "82.5" in block
    assert "recorded_at:" in block
    assert "82 cm" in block
    # Current row must not be re-listed under History
    history_section = block.split("### History", 1)[1]
    assert "身高 82.5" not in history_section
    assert "身高 82 cm" in history_section
    assert history_excluding_current([newer, older], [newer]) == [older]
    assert current_facts_by_key([newer, newer])[0].content == "身高 82.5 cm"


def test_format_case_block_history_empty_when_only_current() -> None:
    case_id = uuid4()
    only = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="height_cm",
        content="身高 82.5 cm",
        tags=["身高"],
        status="confirmed",
        updated_at=datetime(2026, 8, 7, 3, 44, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 3, 44, tzinfo=UTC),
    )
    block = format_case_block([only], history=[only])
    assert block is not None
    assert "(none)" in block.split("### History", 1)[1]


def test_memory_excludes_case_keys() -> None:
    memory = UserMemory(
        id=uuid4(),
        user_id=uuid4(),
        agent_id=IMD_AGENT_ID,
        kind="profile",
        key="height_cm",
        content="身高 82 cm",
        tags=["身高"],
        status="active",
    )
    assert format_memory_block([memory], exclude_keys={"height_cm"}) is None
    block = format_memory_block([memory], exclude_keys=set())
    assert block is not None
    assert "82" in block


@pytest.mark.anyio
async def test_load_case_block_only_confirmed_current(database_session: AsyncSession) -> None:
    user = User(email=f"case-recall-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    database_session.add_all(
        [
            CaseFact(
                id=uuid4(),
                case_id=case_id,
                key="height_cm",
                content="身高 80 cm",
                tags=["身高"],
                status="confirmed",
            ),
            CaseFact(
                id=uuid4(),
                case_id=case_id,
                key="weight_kg",
                content="体重 10 kg",
                tags=["体重"],
                status="proposed",
            ),
        ],
    )
    await database_session.flush()

    block = await load_case_block(database_session, case_id=case_id)
    assert block is not None
    assert "80" in block
    assert "10 kg" not in block
    assert "weight_kg" not in block
