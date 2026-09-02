import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.case.extract import (
    CaseFactUpdate,
    ExtractedCasePayload,
    apply_attribution_policy,
    apply_case_extract,
    extract_case_via_background,
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
    # Unsupervised extraction never auto-confirms, even for attribution=="self" —
    # only an explicit HITL approval (already_approved=True) may confirm it.
    assert apply_attribution_policy(self_payload) == "propose"
    assert apply_attribution_policy(self_payload, already_approved=True) == "confirm"

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


def test_parse_normalizes_a_bare_diagnosis_gene() -> None:
    raw: dict[str, object] = {
        "attribution": "self",
        "updates": [{"key": "diagnosis_subtype", "content": "MMUT", "tags": []}],
    }
    payload = parse_case_extract_payload(raw)

    assert payload.updates == [
        CaseFactUpdate(
            key="diagnosis_subtype",
            content="诊断分型/基因 MMUT",
            tags=["诊断分型"],
        ),
    ]


def test_slot_hints_cover_sex_dob_and_age() -> None:
    hints = {
        item.key: item.content
        for item in slot_hints_from_user_message("男宝，2024年3月5日出生，现在29个月啦")
    }
    assert hints["sex"] == "性别 男"
    assert hints["date_of_birth"] == "出生日期 2024-03-05"
    assert hints["age_months"] == "月龄 29 个月"

    girl = {item.key: item.content for item in slot_hints_from_user_message("我家小女孩2岁5个月")}
    assert girl["sex"] == "性别 女"
    assert girl["age_months"] == "月龄 29 个月"

    iso = {item.key: item.content for item in slot_hints_from_user_message("宝宝生日 2024-3-5")}
    assert iso["date_of_birth"] == "出生日期 2024-03-05"


def test_slot_hints_ignore_non_age_months_and_contextless_dates() -> None:
    assert slot_hints_from_user_message("3个月后复查") == []
    hints = slot_hints_from_user_message("2024年3月5日复查")
    assert all(item.key != "date_of_birth" for item in hints)


def test_slot_hints_capture_diagnosis_subtype_and_gene() -> None:
    subtype = {
        item.key: item.content
        for item in slot_hints_from_user_message("宝宝确诊了孤立型甲基丙二酸血症")
    }
    assert subtype["diagnosis_subtype"] == "诊断分型/基因 孤立型甲基丙二酸血症"

    gene = {
        item.key: item.content for item in slot_hints_from_user_message("基因检测结果是MMUT突变")
    }
    assert gene["diagnosis_subtype"] == "诊断分型/基因 MMUT"

    both = {
        item.key: item.content for item in slot_hints_from_user_message("确诊丙酸血症，基因是PCCA")
    }
    assert both["diagnosis_subtype"] == "诊断分型/基因 丙酸血症、PCCA"

    assert infer_case_fact_key("孤立型甲基丙二酸血症", []) == "diagnosis_subtype"
    assert infer_case_fact_key("普通话题没有诊断信息", []) is None


def test_self_context_upgrades_model_updates_without_regex_hints() -> None:
    payload = ExtractedCasePayload(
        attribution="unknown",
        updates=[CaseFactUpdate(key=None, content="对花生过敏", tags=["过敏"])],
    )
    merged = merge_user_slot_hints("宝宝对花生过敏怎么办", payload)
    assert merged.attribution == "self"
    assert merged.updates == payload.updates


@pytest.mark.anyio
async def test_extract_case_via_background_forces_json_and_strips_think() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<think>用户在说自家宝宝</think>"
                                '{"attribution":"self","updates":'
                                '[{"key":"sex","content":"性别 男","tags":["性别"]}]}'
                            ),
                        },
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await extract_case_via_background("男宝", "好的", client)

    assert captured["response_format"] == {"type": "json_object"}
    assert payload.attribution == "self"
    assert payload.updates[0].key == "sex"


@pytest.mark.anyio
async def test_unsupervised_self_proposes_and_other_skips(
    database_session: AsyncSession,
) -> None:
    """Background extraction (no HITL) must never auto-confirm, even for self."""

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
        user_id=user.id,
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
        user_id=user.id,
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

    assert (
        await database_session.scalar(
            select(CaseFact).where(
                CaseFact.case_id == case_id,
                CaseFact.status == "confirmed",
            ),
        )
    ) is None
    proposed = list(
        await database_session.scalars(
            select(CaseFact).where(
                CaseFact.case_id == case_id,
                CaseFact.status == "proposed",
            ),
        ),
    )
    assert len(proposed) == 1
    assert "91" in proposed[0].content


@pytest.mark.anyio
async def test_approved_self_confirms(database_session: AsyncSession) -> None:
    """The HITL-approved path (case_attribution_confirm) still writes confirmed."""

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
        user_id=user.id,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="self",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 91 cm", tags=["身高"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
        already_approved=True,
    )
    assert written == 1
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
        user_id=user.id,
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
        user_id=user.id,
        case_id=case_id,
        payload=payload,
        source_thread_id=None,
        source_run_id=None,
        already_approved=True,
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
        user_id=user.id,
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
        already_approved=True,
    )
    await apply_case_extract(
        database_session,
        user_id=user.id,
        case_id=case_id,
        payload=ExtractedCasePayload(
            attribution="self",
            updates=[
                CaseFactUpdate(key="height_cm", content="身高 82.5 cm", tags=["身高"]),
            ],
        ),
        source_thread_id=None,
        source_run_id=None,
        already_approved=True,
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
