"""Upload artifact reference parsing and scoped run-context tests."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.artifact_store import create_artifact
from agent_api.db.case_store import create_case
from agent_api.db.models import User
from agent_api.uploads.context import load_upload_injection, parse_artifact_ids


def test_parse_artifact_ids_extracts_complete_uuid_references_in_order() -> None:
    first = UUID("11111111-2222-3333-4444-555555555555")
    second = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    parsed = parse_artifact_ids(
        "分析 artifact_id=11111111-2222-3333-4444-555555555555，"
        "并比较 ARTIFACT_ID = aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee；"
        "忽略 artifact_id=not-a-uuid 和 artifact_id=11111111-2222-3333-4444。"
    )

    assert parsed == [first, second]


def test_parse_artifact_ids_rejects_uuid_embedded_in_a_larger_token() -> None:
    parsed = parse_artifact_ids(
        "xartifact_id=11111111-2222-3333-4444-555555555555 "
        "artifact_id=11111111-2222-3333-4444-555555555555suffix"
    )

    assert parsed == []


@pytest.mark.anyio
async def test_load_upload_injection_includes_only_owned_artifact_in_current_case(
    database_session: AsyncSession,
) -> None:
    owner = User(email=f"upload-context-{uuid4().hex}@example.com", status="active")
    database_session.add(owner)
    await database_session.flush()
    agent_id = UUID("00000000-0000-0000-0000-000000000002")
    current_case = await create_case(
        database_session,
        user_id=owner.id,
        agent_id=agent_id,
        display_name="当前患者",
        make_default=False,
    )
    other_case = await create_case(
        database_session,
        user_id=owner.id,
        agent_id=agent_id,
        display_name="其他患者",
        make_default=False,
    )
    visible = await create_artifact(
        database_session,
        owner_user_id=owner.id,
        case_id=current_case.id,
        kind="upload",
        title="血液检查",
        content="0123456789",
        mime_type="application/pdf",
    )
    hidden = await create_artifact(
        database_session,
        owner_user_id=owner.id,
        case_id=other_case.id,
        kind="upload",
        title="其他患者报告",
        content="private",
        mime_type="image/png",
    )

    block = await load_upload_injection(
        database_session,
        owner_user_id=owner.id,
        case_id=current_case.id,
        user_text=f"artifact_id={visible.id} artifact_id={hidden.id}",
        preview_chars=5,
    )

    assert block is not None
    assert "血液检查" in block
    assert "application/pdf" in block
    assert "01234" in block
    assert "012345" not in block
    assert str(visible.id) in block
    assert "read_artifact" in block
    assert "其他患者报告" not in block
    assert str(hidden.id) not in block


@pytest.mark.anyio
async def test_load_upload_injection_returns_none_without_visible_references(
    database_session: AsyncSession,
) -> None:
    block = await load_upload_injection(
        database_session,
        owner_user_id=uuid4(),
        case_id=None,
        user_text="没有附件引用",
    )

    assert block is None
