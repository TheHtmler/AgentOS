from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.chat_store import (
    append_text_delta,
    append_tool_call_event,
    append_tool_result_event,
    complete_run,
    fail_orphaned_in_process_runs,
    list_completed_run_message_histories,
    list_thread_messages,
    list_thread_tool_calls,
    list_threads,
    pause_run_for_approval,
    start_run,
)
from agent_api.db.models import Agent, Run, RunEvent, RunMessageHistory, Thread, User
from agent_api.hitl_types import ApprovalRequest


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
async def test_tool_events_are_appended_for_web_search(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="最近新闻",
            model_name="gemma4:e4b",
        )
        await append_tool_call_event(
            session,
            run_id=started.run_id,
            tool_name="web_search",
            args={"query": "最近新闻", "max_results": 5},
        )
        await append_tool_result_event(
            session,
            run_id=started.run_id,
            tool_name="web_search",
            provider="duckduckgo",
            ok=True,
            summary="duckduckgo: 1 results",
        )

        events = list(
            (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == started.run_id)
                    .order_by(RunEvent.seq),
                )
            ).all()
        )
        assert [(event.seq, event.event_type) for event in events] == [
            (1, "run_started"),
            (2, "tool_call"),
            (3, "tool_result"),
        ]
        assert events[2].payload["provider"] == "duckduckgo"
        assert events[2].payload["ok"] is True
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_list_thread_tool_calls_anchors_to_matching_user_message(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        first = await start_run(
            session,
            thread_id=None,
            user_content="第一轮搜索",
            model_name="gemma4:e4b",
        )
        await append_tool_call_event(
            session,
            run_id=first.run_id,
            tool_name="web_search",
            args={"query": "第一轮搜索", "max_results": 5},
        )
        await append_tool_result_event(
            session,
            run_id=first.run_id,
            tool_name="web_search",
            provider="duckduckgo",
            ok=True,
            summary="duckduckgo: 2 results",
            metadata={"files": [{"path": "joke.txt", "size": 5, "mime_type": "text/plain"}]},
        )
        await complete_run(session, run_id=first.run_id, assistant_content="第一轮回答")

        second = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第二轮不搜索",
            model_name="gemma4:e4b",
        )
        await complete_run(session, run_id=second.run_id, assistant_content="第二轮回答")

        third = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第三轮失败搜索",
            model_name="gemma4:e4b",
        )
        await append_tool_call_event(
            session,
            run_id=third.run_id,
            tool_name="web_search",
            args={"query": "第三轮失败搜索", "max_results": 3},
        )
        await append_tool_result_event(
            session,
            run_id=third.run_id,
            tool_name="web_search",
            provider="tavily",
            ok=False,
            summary="tavily rate limited",
        )
        await complete_run(session, run_id=third.run_id, assistant_content="第三轮回答")

        messages = await list_thread_messages(session, thread_id=first.thread_id)
        user_ids = [message.id for message in messages if message.role == "user"]
        tool_calls = await list_thread_tool_calls(session, thread_id=first.thread_id)

        assert len(user_ids) == 3
        assert len(tool_calls) == 2
        assert tool_calls[0].tool_name == "web_search"
        assert tool_calls[0].status == "done"
        assert tool_calls[0].provider == "duckduckgo"
        assert tool_calls[0].summary == "duckduckgo: 2 results"
        assert tool_calls[0].after_message_id == user_ids[0]
        assert tool_calls[0].files == [
            {"path": "joke.txt", "size": 5, "mime_type": "text/plain"},
        ]
        assert tool_calls[0].args == {"query": "第一轮搜索", "max_results": 5}
        assert tool_calls[1].status == "error"
        assert tool_calls[1].provider == "tavily"
        assert tool_calls[1].after_message_id == user_ids[2]
    finally:
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


@pytest.mark.anyio
async def test_start_run_binds_requested_agent_id(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        parenting_id = await session.scalar(select(Agent.id).where(Agent.slug == "imd"))
        assert parenting_id is not None

        started = await start_run(
            session,
            thread_id=None,
            user_content="育儿问题",
            model_name="gemma4:e4b",
            agent_id=parenting_id,
        )
        thread = await session.get(Thread, started.thread_id)

        assert thread is not None
        assert thread.agent_id == parenting_id
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_list_threads_filters_by_agent(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        user = User(email="agent-filter@example.com", status="active")
        session.add(user)
        await session.flush()
        general_id = await session.scalar(select(Agent.id).where(Agent.slug == "general"))
        parenting_id = await session.scalar(select(Agent.id).where(Agent.slug == "imd"))
        assert general_id is not None
        assert parenting_id is not None

        general = await start_run(
            session,
            thread_id=None,
            user_content="通用问题",
            model_name="gemma4:e4b",
            user_id=user.id,
            agent_id=general_id,
        )
        parenting = await start_run(
            session,
            thread_id=None,
            user_content="育儿问题",
            model_name="gemma4:e4b",
            user_id=user.id,
            agent_id=parenting_id,
        )

        threads = await list_threads(
            session,
            limit=20,
            user_id=user.id,
            agent_id=parenting_id,
        )

        assert [thread.id for thread in threads] == [parenting.thread_id]
        assert general.thread_id not in {thread.id for thread in threads}
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_existing_thread_ignores_requested_agent_id(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        general_id = await session.scalar(select(Agent.id).where(Agent.slug == "general"))
        parenting_id = await session.scalar(select(Agent.id).where(Agent.slug == "imd"))
        assert general_id is not None
        assert parenting_id is not None
        first = await start_run(
            session,
            thread_id=None,
            user_content="第一轮",
            model_name="gemma4:e4b",
            agent_id=general_id,
        )
        await complete_run(session, run_id=first.run_id, assistant_content="第一轮回答")

        await start_run(
            session,
            thread_id=first.thread_id,
            user_content="第二轮",
            model_name="gemma4:e4b",
            agent_id=parenting_id,
        )
        thread = await session.get(Thread, first.thread_id)

        assert thread is not None
        assert thread.agent_id == general_id
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_fail_orphaned_in_process_runs_spares_waiting_approval(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        orphaned = await start_run(
            session,
            thread_id=None,
            user_content="重启前卡住",
            model_name="gemma4:e4b",
        )
        waiting = await start_run(
            session,
            thread_id=None,
            user_content="待审批",
            model_name="gemma4:e4b",
        )
        await pause_run_for_approval(
            session,
            run_id=waiting.run_id,
            approvals=[
                ApprovalRequest(
                    tool_call_id="call-1",
                    tool_name="case_slot_collect",
                    tool_args={"fields_json": "[]"},
                ),
            ],
            model_messages=[{"kind": "model-request", "parts": []}],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

        count = await fail_orphaned_in_process_runs(session)
        assert count == 1

        orphaned_run = await session.get(Run, orphaned.run_id)
        waiting_run = await session.get(Run, waiting.run_id)
        assert orphaned_run is not None
        assert orphaned_run.status == "failed"
        assert orphaned_run.error_message is not None
        assert "restarted" in orphaned_run.error_message
        assert waiting_run is not None
        assert waiting_run.status == "waiting_approval"

        # Thread is free for a new run after orphan cleanup.
        again = await start_run(
            session,
            thread_id=orphaned.thread_id,
            user_content="可以再发",
            model_name="gemma4:e4b",
        )
        assert again.thread_id == orphaned.thread_id
    finally:
        await transaction.rollback()
