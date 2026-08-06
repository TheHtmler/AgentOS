from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.recall import CASE_HEADER, format_case_block, load_case_block
from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_format_case_block_empty() -> None:
    assert format_case_block([]) is None


def test_format_case_block_renders_keys() -> None:
    fact = CaseFact(
        id=uuid4(),
        case_id=uuid4(),
        key="height_cm",
        content="身高 78 cm",
        tags=["身高"],
        status="confirmed",
    )
    block = format_case_block([fact])
    assert block is not None
    assert block.startswith(CASE_HEADER)
    assert "[height_cm]" in block
    assert "78" in block


@pytest.mark.anyio
async def test_load_case_block_only_confirmed(database_session: AsyncSession) -> None:
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
    assert "体重" not in block
