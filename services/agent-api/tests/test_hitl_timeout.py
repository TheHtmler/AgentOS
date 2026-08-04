"""HITL approval timeout sweep."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests
from sqlalchemy import select

from agent_api.db.models import Interrupt, Thread
from agent_api.db.session import close_database, session_factory
from agent_api.hitl_timeout import sweep_expired_approvals
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


@pytest.mark.anyio
async def test_sweep_expired_approvals_auto_denies(authenticated_api_user: UUID) -> None:
    runtime = AgentRuntime(
        agent=Agent(
            TestModel(),
            deps_type=object,
            tools=[Tool(_need_approval, requires_approval=True)],
            output_type=[str, DeferredToolRequests],
        ),
        model_semaphore=asyncio.Semaphore(1),
    )
    app.state.runtime = runtime
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/ag-ui/runs",
                json={
                    "threadId": "new",
                    "runId": "browser-provisional-run",
                    "state": {},
                    "messages": [
                        {
                            "id": "m1",
                            "role": "user",
                            "content": "fetch please",
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

        async with session_factory() as session, session.begin():
            interrupt = await session.scalar(
                select(Interrupt).where(Interrupt.run_id == run_id),
            )
            assert interrupt is not None
            interrupt.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        started = await sweep_expired_approvals(runtime)
        assert started == 1

        for _ in range(40):
            async with session_factory() as session:
                interrupt = await session.scalar(
                    select(Interrupt).where(Interrupt.run_id == run_id),
                )
                from agent_api.db.models import Run

                run = await session.get(Run, run_id)
            assert interrupt is not None
            assert run is not None
            if interrupt.status == "timed_out" and run.status in {"completed", "failed", "running"}:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("timeout resume did not progress")

        assert interrupt.status == "timed_out"

        for _ in range(40):
            async with session_factory() as session:
                from agent_api.db.models import Run

                run = await session.get(Run, run_id)
            if run is not None and run.status in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.05)
        await runtime.stop_background_runs()
    finally:
        await runtime.stop_background_runs()
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)
