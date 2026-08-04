"""HITL resume / cancel API integration tests."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests

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


async def _need_approval(_ctx: RunContext[None], url: str) -> str:
    return f"fetched {url}"


def _approval_agent() -> Agent[None, str | DeferredToolRequests]:
    return Agent(
        TestModel(),
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
