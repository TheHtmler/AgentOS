from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.case_store import (
    CaseNotFoundError,
    ensure_default_case,
    resolve_case_for_new_thread,
    user_can_access_case,
)
from agent_api.db.chat_store import start_run
from agent_api.db.models import Agent, Case, CaseMembership, Run, Thread, User, UserAgentDefaultCase

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
GENERAL_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.anyio
async def test_ensure_default_case_creates_once(database_session: AsyncSession) -> None:
    user = User(email=f"case-owner-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()

    first = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    second = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    assert first == second

    case = await database_session.get(Case, first)
    assert case is not None
    assert case.display_name == "默认档案"
    assert case.owner_user_id == user.id

    membership = await database_session.scalar(
        select(CaseMembership).where(
            CaseMembership.case_id == first,
            CaseMembership.user_id == user.id,
        ),
    )
    assert membership is not None
    assert membership.role == "owner"


@pytest.mark.anyio
async def test_resolve_ignores_case_when_disabled(database_session: AsyncSession) -> None:
    user = User(email=f"case-off-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()

    resolved = await resolve_case_for_new_thread(
        database_session,
        user_id=user.id,
        agent_id=GENERAL_AGENT_ID,
        case_id=None,
        case_enabled=False,
    )
    assert resolved is None


@pytest.mark.anyio
async def test_resolve_rejects_inaccessible_case(database_session: AsyncSession) -> None:
    owner = User(email=f"case-a-{uuid4().hex}@example.com", status="active")
    other = User(email=f"case-b-{uuid4().hex}@example.com", status="active")
    database_session.add_all([owner, other])
    await database_session.flush()

    foreign_case_id = await ensure_default_case(
        database_session,
        user_id=owner.id,
        agent_id=IMD_AGENT_ID,
    )

    with pytest.raises(CaseNotFoundError):
        await resolve_case_for_new_thread(
            database_session,
            user_id=other.id,
            agent_id=IMD_AGENT_ID,
            case_id=foreign_case_id,
            case_enabled=True,
        )

    assert not await user_can_access_case(
        database_session,
        user_id=other.id,
        case_id=foreign_case_id,
    )


@pytest.mark.anyio
async def test_start_run_binds_case_for_imd(database_session: AsyncSession) -> None:
    user = User(email=f"case-run-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()

    started = await start_run(
        database_session,
        thread_id=None,
        user_content="孩子身高",
        model_name="test-model",
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    thread = await database_session.get(Thread, started.thread_id)
    run = await database_session.get(Run, started.run_id)
    assert thread is not None
    assert run is not None
    assert thread.case_id is not None
    assert run.case_id == thread.case_id
    assert await user_can_access_case(
        database_session,
        user_id=user.id,
        case_id=thread.case_id,
    )

    default = await database_session.scalar(
        select(UserAgentDefaultCase).where(
            UserAgentDefaultCase.user_id == user.id,
            UserAgentDefaultCase.agent_id == IMD_AGENT_ID,
        ),
    )
    assert default is not None
    assert default.case_id == thread.case_id


@pytest.mark.anyio
async def test_start_run_skips_case_for_general(database_session: AsyncSession) -> None:
    user = User(email=f"case-general-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()

    agent = await database_session.get(Agent, GENERAL_AGENT_ID)
    assert agent is not None

    started = await start_run(
        database_session,
        thread_id=None,
        user_content="hello",
        model_name="test-model",
        user_id=user.id,
        agent_id=GENERAL_AGENT_ID,
    )
    thread = await database_session.get(Thread, started.thread_id)
    assert thread is not None
    assert thread.case_id is None
