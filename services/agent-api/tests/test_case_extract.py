from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.extract import (
    CaseFactUpdate,
    ExtractedCasePayload,
    apply_attribution_policy,
    apply_case_extract,
    infer_case_fact_key,
    merge_user_slot_hints,
    parse_case_extract_payload,
    slot_hints_from_user_message,
)
from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_infer_key_and_user_slot_hints() -> None:
    assert infer_case_fact_key("身高 82 cm", ["身高"]) == "height_cm"
    assert infer_case_fact_key("体重 15.2 kg", []) == "weight_kg"
    hints = slot_hints_from_user_message("目前宝宝身高82.5cm，体重15.2kg")
    assert {item.key for item in hints} == {"height_cm", "weight_kg"}

    partial = ExtractedCasePayload(
        attribution="unknown",
        updates=[CaseFactUpdate(key="height_cm", content="身高 82.5 cm", tags=["身高"])],
    )
    merged = merge_user_slot_hints("目前宝宝身高82.5cm，体重15.2kg", partial)
    assert merged.attribution == "self"
    assert {item.key for item in merged.updates} == {"height_cm", "weight_kg"}

    keyless = parse_case_extract_payload(
        {
            "attribution": "self",
            "updates": [{"key": None, "content": "体重 14 kg", "tags": ["体重"]}],
        },
    )
    assert keyless.updates[0].key == "weight_kg"


def test_parse_and_policy_branches() -> None:
    self_payload = parse_case_extract_payload(
        {
            "attribution": "self",
            "updates": [{"key": "height_cm", "content": "身高 90 cm", "tags": ["身高"]}],
        },
    )
    assert apply_attribution_policy(self_payload) == "confirm"

    other = parse_case_extract_payload(
        {
            "attribution": "other",
            "updates": [{"key": "height_cm", "content": "身高 90 cm", "tags": ["身高"]}],
        },
    )
    assert apply_attribution_policy(other) == "skip"

    hypo = ExtractedCasePayload(
        attribution="hypothetical",
        updates=[CaseFactUpdate(key="height_cm", content="x", tags=["身高"])],
    )
    assert apply_attribution_policy(hypo) == "skip"

    unknown = ExtractedCasePayload(
        attribution="unknown",
        updates=[CaseFactUpdate(key="height_cm", content="x", tags=["身高"])],
    )
    assert apply_attribution_policy(unknown) == "propose"

    empty = ExtractedCasePayload(attribution="self", updates=[])
    assert apply_attribution_policy(empty) == "skip"


@pytest.mark.anyio
async def test_apply_self_confirms_and_other_skips(database_session: AsyncSession) -> None:
    user = User(email=f"case-extract-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )

    written = await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="self",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 91 cm", tags=["身高"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    assert written == 1

    skipped = await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="other",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 120 cm", tags=["身高"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    assert skipped == 0
    await database_session.flush()

    facts = list(
        await database_session.scalars(
            select(CaseFact).where(
                CaseFact.case_id == case_id,
                CaseFact.status == "confirmed",
            ),
        ),
    )
    assert len(facts) == 1
    assert "91" in facts[0].content


@pytest.mark.anyio
async def test_unknown_writes_proposed(database_session: AsyncSession) -> None:
    user = User(email=f"case-unknown-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="unknown",
            updates=[CaseFactUpdate(key="weight_kg", content="体重 12 kg", tags=["体重"])],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()
    proposed = await database_session.scalar(
        select(CaseFact).where(
            CaseFact.case_id == case_id,
            CaseFact.key == "weight_kg",
            CaseFact.status == "proposed",
        ),
    )
    assert proposed is not None


@pytest.mark.anyio
async def test_merge_fills_missing_weight_then_confirms(database_session: AsyncSession) -> None:
    user = User(email=f"case-merge-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    payload = merge_user_slot_hints(
        "宝宝身高82cm体重15.2kg",
        ExtractedCasePayload(
            attribution="unknown",
            updates=[CaseFactUpdate(key="height_cm", content="身高 82 cm", tags=["身高"])],
        ),
    )
    written = await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=payload,
        source_thread_id=None,
        source_run_id=None,
    )
    assert written == 2
    await database_session.flush()
    confirmed = {
        fact.key: fact.content
        for fact in await database_session.scalars(
            select(CaseFact).where(
                CaseFact.case_id == case_id,
                CaseFact.status == "confirmed",
            ),
        )
    }
    assert "82" in confirmed["height_cm"]
    assert "15.2" in confirmed["weight_kg"]


@pytest.mark.anyio
async def test_height_update_preserves_weight(database_session: AsyncSession) -> None:
    user = User(email=f"case-preserve-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="self",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 82 cm", tags=["身高"]),
                CaseFactUpdate(key="weight_kg", content="体重 15.2 kg", tags=["体重"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    await apply_case_extract(
        database_session,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="self",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 82.5 cm", tags=["身高"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    confirmed = {
        fact.key: fact.content
        for fact in await database_session.scalars(
            select(CaseFact).where(
                CaseFact.case_id == case_id,
                CaseFact.status == "confirmed",
            ),
        )
    }
    assert "82.5" in confirmed["height_cm"]
    assert "15.2" in confirmed["weight_kg"]
