"""Case age recaps must not create or surface redundant approval proposals."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.extract import CaseFactUpdate, slot_hints_from_user_message, upsert_case_fact
from agent_api.case.values import case_slot_values_match
from agent_api.db.case_store import list_facts_for_case, list_proposed_facts
from agent_api.db.models import CaseFact


@pytest.mark.parametrize(
    ("existing", "candidate", "matches"),
    [
        ("月龄 18.1 个月", "约18月龄", True),
        ("约18月龄", "18.1个月", True),
        ("18.1个月", "18个月左右", True),
        ("18.14个月", "约18.1个月", True),
        ("18.1个月", "18.2个月", False),
        ("18.1个月", "18个月", False),
        ("18.1个月", "约19个月", False),
        ("18.4个月", "约18.1个月", False),
        ("18.1个月", "约18.0个月", False),
    ],
)
def test_age_rounding_requires_explicit_approximation(
    existing: str, candidate: str, matches: bool
) -> None:
    assert case_slot_values_match("age_months", existing, candidate) is matches


def test_measurement_changes_are_not_rounded_away() -> None:
    assert not case_slot_values_match("weight_kg", "18.1 kg", "约18 kg")
    assert not case_slot_values_match("height_cm", "82.1 cm", "约82 cm")
    assert case_slot_values_match("weight_kg", "体重 15.2 kg", "15.20kg")


@pytest.mark.parametrize(
    ("text", "content"),
    [
        ("宝宝18.1个月", "月龄 18.1 个月"),
        ("宝宝约18个月", "月龄 约 18 个月"),
        ("孩子18个月左右", "月龄 约 18 个月"),
        ("18.1月龄", "月龄 18.1 个月"),
        ("宝宝1岁6个月", "月龄 18 个月"),
    ],
)
def test_user_age_hints_keep_decimals_and_approximation(text: str, content: str) -> None:
    hints = slot_hints_from_user_message(text)
    assert [hint.content for hint in hints if hint.key == "age_months"] == [content]


@pytest.mark.anyio
async def test_rounded_proposal_does_not_write_or_change_confirmed_age() -> None:
    case_id = uuid4()
    stamp = datetime(2026, 9, 6, tzinfo=UTC)
    current = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="age_months",
        content="月龄18.1个月",
        tags=[],
        status="confirmed",
        updated_at=stamp,
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalars.return_value = [current]
    written = await upsert_case_fact(
        session,
        case_id=case_id,
        fact_update=CaseFactUpdate(key="age_months", content="约18月龄", tags=[]),
        status="proposed",
        source_thread_id=None,
        source_run_id=None,
    )
    assert not written
    session.add.assert_not_called()
    session.execute.assert_not_awaited()
    assert current.content == "月龄18.1个月"
    assert current.updated_at == stamp
    assert current.status == "confirmed"


@pytest.mark.anyio
@pytest.mark.parametrize("reader", [list_proposed_facts, list_facts_for_case])
async def test_existing_rounded_proposals_are_hidden_from_banner_and_context(
    reader: object,
) -> None:
    case_id = uuid4()
    current = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="age_months",
        content="18.1个月",
        tags=[],
        status="confirmed",
    )
    redundant = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="age_months",
        content="约18月龄",
        tags=[],
        status="proposed",
    )
    changed = CaseFact(
        id=uuid4(),
        case_id=case_id,
        key="age_months",
        content="19个月",
        tags=[],
        status="proposed",
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalars.side_effect = [[redundant, changed], [current]]
    if reader is list_proposed_facts:
        result = await list_proposed_facts(session, case_id=case_id)
    else:
        result = await list_facts_for_case(session, case_id=case_id)
    assert result == [changed]
    assert redundant.status == "proposed"
    session.execute.assert_not_awaited()
