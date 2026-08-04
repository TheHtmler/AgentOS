"""HITL interrupt persistence and run pause helpers."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.config import get_settings
from agent_api.db.chat_store import (
    InterruptDecisionError,
    InvalidRunStateError,
    ThreadBusyError,
    apply_interrupt_decisions,
    get_run_message_history,
    list_pending_interrupts,
    pause_run_for_approval,
    start_run,
)
from agent_api.db.models import Interrupt, Run
from agent_api.hitl_types import ApprovalRequest, InterruptDecision


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_interrupt_row_roundtrip(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="需要审批的抓取",
            model_name="gemma4:e4b",
        )
        run = await session.get(Run, started.run_id)
        assert run is not None
        run.status = "waiting_approval"

        interrupt = Interrupt(
            id=uuid4(),
            run_id=started.run_id,
            tool_call_id="call_fetch_1",
            tool_name="fetch_url",
            tool_args={"url": "https://example.com"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        session.add(interrupt)
        await session.flush()

        loaded = await session.get(Interrupt, interrupt.id)
        assert loaded is not None
        assert loaded.tool_name == "fetch_url"
        assert loaded.status == "pending"
        assert loaded.tool_args["url"] == "https://example.com"

        refreshed = await session.get(Run, started.run_id)
        assert refreshed is not None
        assert refreshed.status == "waiting_approval"
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_waiting_approval_blocks_new_run(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="第一轮",
            model_name="gemma4:e4b",
        )
        await pause_run_for_approval(
            session,
            run_id=started.run_id,
            approvals=[
                ApprovalRequest(
                    tool_call_id="c1",
                    tool_name="fetch_url",
                    tool_args={"url": "https://example.com"},
                ),
            ],
            model_messages=[{"kind": "model-request", "parts": []}],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

        with pytest.raises(ThreadBusyError):
            await start_run(
                session,
                thread_id=started.thread_id,
                user_content="第二轮应被挡住",
                model_name="gemma4:e4b",
            )
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_pause_writes_interrupts_and_checkpoint(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="pause me",
            model_name="gemma4:e4b",
        )
        messages: list[dict[str, object]] = [
            {
                "kind": "model-request",
                "parts": [{"part_kind": "user-prompt", "content": "hi"}],
            },
        ]
        interrupts = await pause_run_for_approval(
            session,
            run_id=started.run_id,
            approvals=[
                ApprovalRequest(
                    tool_call_id="call_a",
                    tool_name="fetch_url",
                    tool_args={"url": "https://example.com/a"},
                ),
            ],
            model_messages=messages,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        run = await session.get(Run, started.run_id)
        assert run is not None
        assert run.status == "waiting_approval"
        assert len(interrupts) == 1
        pending = await list_pending_interrupts(session, run_id=started.run_id)
        assert len(pending) == 1
        assert pending[0].tool_call_id == "call_a"
        checkpoint = await get_run_message_history(session, run_id=started.run_id)
        assert checkpoint == messages
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_apply_decisions_idempotent(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="approve me",
            model_name="gemma4:e4b",
        )
        await pause_run_for_approval(
            session,
            run_id=started.run_id,
            approvals=[
                ApprovalRequest(
                    tool_call_id="call_b",
                    tool_name="web_search",
                    tool_args={"query": "x"},
                ),
            ],
            model_messages=[{"kind": "model-request", "parts": []}],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        first = await apply_interrupt_decisions(
            session,
            run_id=started.run_id,
            decisions=[
                InterruptDecision(tool_call_id="call_b", decision="approve"),
            ],
            idempotency_key="key-1",
        )
        assert first[0].status == "approved"
        run = await session.get(Run, started.run_id)
        assert run is not None
        assert run.status == "running"

        second = await apply_interrupt_decisions(
            session,
            run_id=started.run_id,
            decisions=[
                InterruptDecision(tool_call_id="call_b", decision="approve"),
            ],
            idempotency_key="key-1",
        )
        assert second[0].status == "approved"

        with pytest.raises(InvalidRunStateError):
            await apply_interrupt_decisions(
                session,
                run_id=started.run_id,
                decisions=[
                    InterruptDecision(tool_call_id="call_b", decision="deny"),
                ],
                idempotency_key="key-2",
            )

        with pytest.raises(InterruptDecisionError):
            started2 = await start_run(
                session,
                thread_id=None,
                user_content="partial",
                model_name="gemma4:e4b",
            )
            await pause_run_for_approval(
                session,
                run_id=started2.run_id,
                approvals=[
                    ApprovalRequest(
                        tool_call_id="c1",
                        tool_name="fetch_url",
                        tool_args={},
                    ),
                    ApprovalRequest(
                        tool_call_id="c2",
                        tool_name="fetch_url",
                        tool_args={},
                    ),
                ],
                model_messages=[{"kind": "model-request", "parts": []}],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await apply_interrupt_decisions(
                session,
                run_id=started2.run_id,
                decisions=[InterruptDecision(tool_call_id="c1", decision="approve")],
                idempotency_key="partial-key",
            )
    finally:
        await transaction.rollback()
