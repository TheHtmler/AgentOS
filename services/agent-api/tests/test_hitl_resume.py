"""HITL resume / cancel API integration tests."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import pytest
from ag_ui.core import BaseEvent
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests

from agent_api.db.chat_store import get_run_message_history
from agent_api.db.models import Interrupt, Thread
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


async def _need_approval(_ctx: RunContext[object], url: str) -> str:
    return f"fetched {url}"


def _approval_agent() -> Agent[object, str | DeferredToolRequests]:
    return Agent(
        TestModel(),
        deps_type=object,
        tools=[Tool(_need_approval, requires_approval=True)],
        output_type=[str, DeferredToolRequests],
    )


async def _start_waiting_run(client: AsyncClient) -> tuple[UUID, UUID, str]:
    response = await client.post(
        "/v1/ag-ui/runs",
        json={
            "threadId": "new",
            "runId": "browser-provisional-run",
            "state": {},
            "messages": [
                {
                    "id": "browser-message-1",
                    "role": "user",
                    "content": "请打开 https://example.com",
                },
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )
    assert response.status_code == 200
    thread_id = UUID(response.headers["x-agentos-thread-id"])
    run_id = UUID(response.headers["x-agentos-run-id"])

    detail = await client.get(f"/v1/runs/{run_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "waiting_approval"
    assert len(payload["pending_interrupts"]) == 1
    tool_call_id = payload["pending_interrupts"][0]["tool_call_id"]
    return thread_id, run_id, tool_call_id


async def _wait_for_status(client: AsyncClient, run_id: UUID, *statuses: str) -> dict[str, object]:
    for _ in range(40):
        detail = await client.get(f"/v1/runs/{run_id}")
        assert detail.status_code == 200
        payload = detail.json()
        if payload["status"] in statuses:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {statuses}")


@pytest.mark.anyio
async def test_resume_approve_completes_run(authenticated_api_user: UUID) -> None:
    app.state.runtime = AgentRuntime(
        agent=_approval_agent(),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, tool_call_id = await _start_waiting_run(client)
            resume = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "approve-1",
                    "decisions": [
                        {"tool_call_id": tool_call_id, "decision": "approve"},
                    ],
                },
            )
            assert resume.status_code == 200
            assert resume.json()["status"] == "running"

            # Idempotent replay must not 409.
            replay = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "approve-1",
                    "decisions": [
                        {"tool_call_id": tool_call_id, "decision": "approve"},
                    ],
                },
            )
            assert replay.status_code == 200

            final = await _wait_for_status(client, run_id, "completed", "failed")
            assert final["status"] == "completed"

            async with session_factory() as session:
                from sqlalchemy import select

                row = await session.scalar(
                    select(Interrupt).where(Interrupt.run_id == run_id),
                )
                assert row is not None
                assert row.status == "approved"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_resume_checkpoint_stays_snapshot_free(authenticated_api_user: UUID) -> None:
    """The per-run context snapshot reaches the model but must never be persisted."""

    app.state.runtime = AgentRuntime(
        agent=_approval_agent(),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, tool_call_id = await _start_waiting_run(client)
            resume = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "approve-snapshot-free",
                    "decisions": [
                        {"tool_call_id": tool_call_id, "decision": "approve"},
                    ],
                },
            )
            assert resume.status_code == 200
            final = await _wait_for_status(client, run_id, "completed", "failed")
            assert final["status"] == "completed"

            async with session_factory() as session:
                checkpoint = await get_run_message_history(session, run_id=run_id)
            assert checkpoint is not None
            serialized = json.dumps(checkpoint, ensure_ascii=False)
            assert "以下为平台注入的本轮上下文数据" not in serialized
            assert "请打开 https://example.com" in serialized
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_resume_deny_skips_tool_and_completes(authenticated_api_user: UUID) -> None:
    app.state.runtime = AgentRuntime(
        agent=_approval_agent(),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, tool_call_id = await _start_waiting_run(client)
            resume = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "deny-1",
                    "decisions": [
                        {
                            "tool_call_id": tool_call_id,
                            "decision": "deny",
                            "message": "不要抓取",
                        },
                    ],
                },
            )
            assert resume.status_code == 200
            final = await _wait_for_status(client, run_id, "completed", "failed")
            assert final["status"] == "completed"

            async with session_factory() as session:
                from sqlalchemy import select

                row = await session.scalar(
                    select(Interrupt).where(Interrupt.run_id == run_id),
                )
                assert row is not None
                assert row.status == "denied"
                assert row.decision_message == "不要抓取"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_cancel_waiting_approval(authenticated_api_user: UUID) -> None:
    app.state.runtime = AgentRuntime(
        agent=_approval_agent(),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, _tool_call_id = await _start_waiting_run(client)
            cancel = await client.post(f"/v1/runs/{run_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["status"] == "cancelled"

            async with session_factory() as session:
                from sqlalchemy import select

                row = await session.scalar(
                    select(Interrupt).where(Interrupt.run_id == run_id),
                )
                assert row is not None
                assert row.status == "cancelled"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


def _latched_approval_agent(
    resume_latch: asyncio.Event,
    *,
    resume_calls_tool_again: bool = False,
) -> tuple[Agent[object, str | DeferredToolRequests], list[int]]:
    """FunctionModel agent: first request triggers the approval tool; the
    resume request waits on ``resume_latch`` so tests can subscribe before
    the continuation emits anything."""

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    invocations: list[int] = []

    async def stream_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        invocations.append(1)
        if len(invocations) == 1:
            yield {
                0: DeltaToolCall(
                    name="_need_approval",
                    json_args='{"url": "https://example.com"}',
                    tool_call_id="call-approval-1",
                ),
            }
            return
        await resume_latch.wait()
        if resume_calls_tool_again:
            yield {
                0: DeltaToolCall(
                    name="_need_approval",
                    json_args='{"url": "https://example.org"}',
                    tool_call_id="call-approval-2",
                ),
            }
        else:
            yield "已完成抓取。"

    agent: Agent[object, str | DeferredToolRequests] = Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=object,
        tools=[Tool(_need_approval, requires_approval=True)],
        output_type=[str, DeferredToolRequests],
    )
    return agent, invocations


async def _collect_broker_events(
    queue: asyncio.Queue[BaseEvent | None],
) -> list[BaseEvent]:
    events: list[BaseEvent] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=5)
        if item is None:
            return events
        events.append(item)


@pytest.mark.anyio
async def test_resume_streams_events_to_broker_subscriber(
    authenticated_api_user: UUID,
) -> None:
    from ag_ui.core import (
        RunFinishedEvent,
        RunStartedEvent,
        TextMessageContentEvent,
        ToolCallResultEvent,
    )

    resume_latch = asyncio.Event()
    agent, _invocations = _latched_approval_agent(resume_latch)
    runtime = AgentRuntime(agent=agent, model_semaphore=asyncio.Semaphore(1))
    app.state.runtime = runtime
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, tool_call_id = await _start_waiting_run(client)
            assert tool_call_id == "call-approval-1"

            resume = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "approve-stream",
                    "decisions": [{"tool_call_id": tool_call_id, "decision": "approve"}],
                },
            )
            assert resume.status_code == 200

            queue = runtime.run_event_broker.subscribe(run_id)
            assert queue is not None
            resume_latch.set()
            events = await _collect_broker_events(queue)

        assert isinstance(events[0], RunStartedEvent)
        assert events[0].run_id == str(run_id)

        result_events = [event for event in events if isinstance(event, ToolCallResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].tool_call_id == "call-approval-1"
        assert "fetched https://example.com" in result_events[0].content

        streamed_text = "".join(
            event.delta for event in events if isinstance(event, TextMessageContentEvent)
        )
        assert "已完成抓取。" in streamed_text

        assert isinstance(events[-1], RunFinishedEvent)
        assert not runtime.run_event_broker.has_publisher(run_id)

        final = await _wait_for_status(client, run_id, "completed", "failed")
        assert final["status"] == "completed"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_resume_that_defers_again_streams_and_pauses(
    authenticated_api_user: UUID,
) -> None:
    from ag_ui.core import RunFinishedEvent, ToolCallStartEvent

    resume_latch = asyncio.Event()
    agent, _invocations = _latched_approval_agent(resume_latch, resume_calls_tool_again=True)
    runtime = AgentRuntime(agent=agent, model_semaphore=asyncio.Semaphore(1))
    app.state.runtime = runtime
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            thread_id, run_id, tool_call_id = await _start_waiting_run(client)
            resume = await client.post(
                f"/v1/runs/{run_id}/resume",
                json={
                    "idempotency_key": "approve-then-defer",
                    "decisions": [{"tool_call_id": tool_call_id, "decision": "approve"}],
                },
            )
            assert resume.status_code == 200

            queue = runtime.run_event_broker.subscribe(run_id)
            assert queue is not None
            resume_latch.set()
            events = await _collect_broker_events(queue)

            new_calls = [event for event in events if isinstance(event, ToolCallStartEvent)]
            assert any(event.tool_call_id == "call-approval-2" for event in new_calls)
            assert isinstance(events[-1], RunFinishedEvent)

            final = await _wait_for_status(client, run_id, "waiting_approval")
            pending = cast(list[dict[str, object]], final["pending_interrupts"])
            assert len(pending) == 1
            assert pending[0]["tool_call_id"] == "call-approval-2"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_stream_endpoint_204_when_not_streaming_and_404_for_unknown_run(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=_approval_agent(),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # waiting_approval runs do not publish → 204 (client falls back to polling).
            thread_id, run_id, _tool_call_id = await _start_waiting_run(client)
            waiting = await client.get(f"/v1/runs/{run_id}/stream")
            assert waiting.status_code == 204

            unknown = await client.get(
                "/v1/runs/00000000-0000-0000-0000-000000000099/stream",
            )
            assert unknown.status_code == 404
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)
