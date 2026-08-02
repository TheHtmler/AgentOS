from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.config import get_settings
from agent_api.db.chat_store import (
    append_text_delta,
    complete_run,
    list_completed_run_message_histories,
    list_thread_messages,
    start_run,
)
from agent_api.db.models import Run, RunEvent, RunMessageHistory, Thread


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    """Create an event-loop-local database connection for each AnyIO test."""

    # asyncpg connections cannot be reused across pytest's separate event loops.
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
async def test_run_facts_are_ordered_and_rollback_cleanly(
    database_session: AsyncSession,
) -> None:
    """Exercise PostgreSQL constraints without leaving test data in the development database."""

    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="你好",
            model_name="gemma4:e4b",
        )
        await append_text_delta(
            session,
            run_id=started.run_id,
            delta="你好，",
        )
        await complete_run(
            session,
            run_id=started.run_id,
            assistant_content="你好，AgentOS 已连接。",
            model_messages=[
                {
                    "kind": "model-request",
                    "parts": [],
                },
            ],
        )

        messages = await list_thread_messages(session, thread_id=started.thread_id)
        run = await session.get(Run, started.run_id)
        events = list(
            (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == started.run_id)
                    .order_by(RunEvent.seq),
                )
            ).all()
        )

        histories = await list_completed_run_message_histories(
            session,
            thread_id=started.thread_id,
            limit=get_settings().history_max_runs,
        )

        assert [(message.seq, message.role) for message in messages] == [
            (1, "user"),
            (2, "assistant"),
        ]
        assert run is not None
        assert run.status == "completed"
        assert [(event.seq, event.event_type) for event in events] == [
            (1, "run_started"),
            (2, "text_delta"),
            (3, "run_completed"),
        ]
        assert len(histories) == 1
        assert histories[0].run_id == started.run_id
        assert isinstance(histories[0], RunMessageHistory)
        assert histories[0].messages == [
            {
                "kind": "model-request",
                "parts": [],
            },
        ]
    finally:
        # The test uses a real PostgreSQL transaction but must not retain developer data.
        await transaction.rollback()


@pytest.mark.anyio
async def test_completed_run_history_window_keeps_recent_blocks_in_order(
    database_session: AsyncSession,
) -> None:
    """Keep recent whole Run snapshots and return them in conversation order."""

    session = database_session
    transaction = await session.begin()

    try:
        first = await start_run(
            session,
            thread_id=None,
            user_content="第一轮问题",
            model_name="gemma4:e4b",
        )
        await complete_run(
            session,
            run_id=first.run_id,
            assistant_content="第一轮回答",
            model_messages=[{"kind": "model-request", "parts": []}],
        )

        second = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第二轮问题",
            model_name="gemma4:e4b",
        )
        await complete_run(
            session,
            run_id=second.run_id,
            assistant_content="第二轮回答",
            model_messages=[{"kind": "model-request", "parts": []}],
        )

        third = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第三轮问题",
            model_name="gemma4:e4b",
        )
        await complete_run(
            session,
            run_id=third.run_id,
            assistant_content="第三轮回答",
            model_messages=[{"kind": "model-request", "parts": []}],
        )

        first_run = await session.get(Run, first.run_id)
        second_run = await session.get(Run, second.run_id)
        third_run = await session.get(Run, third.run_id)
        assert first_run is not None
        assert second_run is not None
        assert third_run is not None

        # Make ordering deterministic even though this test uses one transaction.
        completed_at = datetime(2026, 8, 2, tzinfo=UTC)
        first_run.completed_at = completed_at
        second_run.completed_at = completed_at + timedelta(seconds=1)
        third_run.completed_at = completed_at + timedelta(seconds=2)
        await session.flush()

        histories = await list_completed_run_message_histories(
            session,
            thread_id=first.thread_id,
            limit=2,
        )

        assert [history.run_id for history in histories] == [
            second.run_id,
            third.run_id,
        ]
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_existing_thread_does_not_create_an_orphan_thread(
    database_session: AsyncSession,
) -> None:
    """Reusing a thread must append facts to it instead of creating an unused Thread."""

    session = database_session
    transaction = await session.begin()

    try:
        thread_count_before = await session.scalar(
            select(func.count()).select_from(Thread),
        )
        first = await start_run(
            session,
            thread_id=None,
            user_content="第一条消息",
            model_name="gemma4:e4b",
        )
        thread_count_after_first = await session.scalar(
            select(func.count()).select_from(Thread),
        )

        await complete_run(
            session,
            run_id=first.run_id,
            assistant_content="第一轮已完成。",
        )

        continued = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第二条消息",
            model_name="gemma4:e4b",
        )
        thread_count_after_second = await session.scalar(
            select(func.count()).select_from(Thread),
        )

        assert thread_count_after_first == (thread_count_before or 0) + 1
        assert continued.thread_id == first.thread_id
        assert thread_count_after_second == thread_count_after_first
    finally:
        await transaction.rollback()
