import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.extract import CaseFactUpdate, upsert_case_fact
from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User
from agent_api.tools.case.collect import _content_for, _parse_fields
from agent_api.tools.registry import mounted_tool_names

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_case_slot_collect_mounts_when_bound() -> None:
    unbound = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        case_bound=False,
    )
    bound = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        case_bound=True,
    )
    assert "case_slot_collect" not in unbound
    assert "case_slot_collect" in bound


def test_parse_fields_and_content_helpers() -> None:
    fields = _parse_fields(
        json.dumps(
            [{"key": "weight_kg", "label": "体重", "unit": "kg", "reason": "评估需要"}],
            ensure_ascii=False,
        ),
    )
    assert fields == [
        {"key": "weight_kg", "label": "体重", "unit": "kg", "reason": "评估需要"},
    ]
    assert _content_for("weight_kg", "15.2", "体重", "kg") == "体重 15.2 kg"


@pytest.mark.anyio
async def test_case_slot_collect_upsert_path(database_session: AsyncSession) -> None:
    """Persist path used by case_slot_collect after HITL fills values."""

    user = User(email=f"case-collect-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    written = await upsert_case_fact(
        database_session,
        case_id=case_id,
        fact_update=CaseFactUpdate(
            key="weight_kg",
            content=_content_for("weight_kg", "15.2", "体重", "kg"),
            tags=["体重"],
        ),
        status="confirmed",
        source_thread_id=None,
        source_run_id=None,
    )
    assert written is True
    await database_session.flush()
    fact = await database_session.scalar(
        select(CaseFact).where(
            CaseFact.case_id == case_id,
            CaseFact.key == "weight_kg",
            CaseFact.status == "confirmed",
        ),
    )
    assert fact is not None
    assert "15.2" in fact.content
