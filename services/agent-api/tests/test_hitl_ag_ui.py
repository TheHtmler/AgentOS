"""AG-UI pauses into waiting_approval when tools require approval."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests
from sqlalchemy import select

from agent_api.db.chat_store import list_pending_interrupts
from agent_api.db.models import Message, Run, Thread
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


@pytest.mark.anyio
async def test_ag_ui_pauses_when_tool_requires_approval(
    authenticated_api_user: UUID,
) -> None:
    agent = Agent(
        TestModel(),
        tools=[Tool(_need_approval, requires_approval=True)],
        output_type=[str, DeferredToolRequests],
    )
    app.state.runtime = AgentRuntime(
        agent=agent,
        model_semaphore=asyncio.Semaphore(1),
    )
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
        assert '"type":"RUN_FINISHED"' in response.text

        thread_id = UUID(response.headers["x-agentos-thread-id"])
        run_id = UUID(response.headers["x-agentos-run-id"])

        async with session_factory() as session:
            run = await session.get(Run, run_id)
            messages = list(
                (
                    await session.scalars(
                        select(Message).where(Message.thread_id == thread_id).order_by(Message.seq),
                    )
                ).all()
            )
            pending = await list_pending_interrupts(session, run_id=run_id)

        assert run is not None
        assert run.status == "waiting_approval"
        assert [message.role for message in messages] == ["user"]
        assert len(pending) == 1
        assert pending[0].tool_name == "_need_approval"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)
