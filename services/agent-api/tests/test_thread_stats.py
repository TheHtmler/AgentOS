"""Thread run-stats endpoint and its pure aggregation helpers."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.usage import RunUsage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api.chat import extract_cached_input_tokens
from agent_api.db.chat_store import aggregate_thread_stats
from agent_api.db.models import Agent, Run, RunEvent, Thread, User
from agent_api.db.session import close_database
from agent_api.main import app


@pytest.fixture
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


def test_aggregate_thread_stats_sums_and_last_run() -> None:
    first_run_id = uuid4()
    last_run_id = uuid4()
    runs = [
        Run(
            id=first_run_id,
            thread_id=uuid4(),
            status="completed",
            model_name="m",
            input_tokens=100,
            output_tokens=None,
        ),
        Run(
            id=last_run_id,
            thread_id=uuid4(),
            status="completed",
            model_name="m",
            input_tokens=300,
            output_tokens=50,
        ),
    ]
    events = [
        RunEvent(run_id=first_run_id, seq=1, event_type="tool_call", payload={"tool": "a"}),
        RunEvent(
            run_id=first_run_id,
            seq=2,
            event_type="tool_result",
            payload={"duration_ms": 400},
        ),
        RunEvent(
            run_id=first_run_id,
            seq=3,
            event_type="model_step",
            payload={"duration_ms": 2000, "ttft_ms": 800, "cached_input_tokens": 64},
        ),
        RunEvent(
            run_id=last_run_id,
            seq=1,
            event_type="model_step",
            payload={"duration_ms": 1000, "ttft_ms": None, "cached_input_tokens": None},
        ),
    ]

    stats = aggregate_thread_stats(runs, events)

    assert stats.runs_total == 2
    assert stats.tool_calls_total == 1
    assert stats.input_tokens_total == 400
    assert stats.output_tokens_total == 50
    assert stats.model_time_ms_total == 3000
    assert stats.tool_time_ms_total == 400
    assert stats.ttft_ms_avg == 800
    assert stats.last_run is not None
    assert stats.last_run.id == last_run_id
    assert stats.last_run.input_tokens == 300
    assert stats.last_run.ttft_ms is None
    assert stats.last_run.cached_input_tokens is None


def test_aggregate_thread_stats_empty() -> None:
    stats = aggregate_thread_stats([], [])
    assert stats.runs_total == 0
    assert stats.ttft_ms_avg is None
    assert stats.last_run is None


def test_extract_cached_input_tokens() -> None:
    assert extract_cached_input_tokens(RunUsage(input_tokens=100, cache_read_tokens=40)) == 40
    assert (
        extract_cached_input_tokens(
            RunUsage(input_tokens=100, details={"prompt_cache_hit_tokens": 30}),
        )
        == 30
    )
    assert extract_cached_input_tokens(RunUsage(input_tokens=100)) is None


@pytest.mark.anyio
async def test_thread_stats_endpoint(
    authenticated_api_user: UUID,
    database_session: AsyncSession,
    dispose_database_pool: None,
) -> None:
    agent = await database_session.scalar(select(Agent).where(Agent.is_default.is_(True)))
    if agent is None:
        pytest.skip("no default agent seeded in database")

    thread = Thread(user_id=authenticated_api_user, agent_id=agent.id, title="stats-thread")
    database_session.add(thread)
    await database_session.flush()
    run = Run(
        thread_id=thread.id,
        status="completed",
        model_name="test-model",
        input_tokens=1000,
        output_tokens=200,
        model_request_count=2,
    )
    database_session.add(run)
    await database_session.flush()
    database_session.add_all(
        [
            RunEvent(
                run_id=run.id,
                seq=1,
                event_type="tool_call",
                payload={"tool": "web_search"},
            ),
            RunEvent(
                run_id=run.id,
                seq=2,
                event_type="tool_result",
                payload={"tool": "web_search", "duration_ms": 500},
            ),
            RunEvent(
                run_id=run.id,
                seq=3,
                event_type="model_step",
                payload={
                    "duration_ms": 8000,
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "ttft_ms": 1200,
                    "cached_input_tokens": None,
                },
            ),
        ],
    )
    other = User(email=f"stats-other-{uuid4().hex}@example.com", status="active")
    database_session.add(other)
    await database_session.flush()
    other_thread = Thread(user_id=other.id, agent_id=agent.id, title="stats-other-thread")
    database_session.add(other_thread)
    await database_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/v1/threads/{thread.id}/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["runs_total"] == 1
        assert body["tool_calls_total"] == 1
        assert body["input_tokens_total"] == 1000
        assert body["output_tokens_total"] == 200
        assert body["model_time_ms_total"] == 8000
        assert body["tool_time_ms_total"] == 500
        assert body["ttft_ms_avg"] == 1200
        last = body["last_run"]
        assert last["id"] == str(run.id)
        assert last["status"] == "completed"
        assert last["input_tokens"] == 1000
        assert last["ttft_ms"] == 1200
        assert last["cached_input_tokens"] is None
        assert "context_window" in last

        # Cross-owner access is indistinguishable from a missing thread.
        missing = await client.get(f"/v1/threads/{other_thread.id}/stats")
        assert missing.status_code == 404
