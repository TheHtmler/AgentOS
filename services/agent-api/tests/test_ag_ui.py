import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

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


@pytest.mark.anyio
async def test_ag_ui_stream_creates_a_server_owned_run(authenticated_api_user: UUID) -> None:
    """AG-UI accepts only the current user turn; history remains server-owned."""

    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="AG-UI 已连接。")),
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
                            "content": "你好",
                        },
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "x-agentos-thread-id" in response.headers
        assert "x-agentos-run-id" in response.headers
        assert '"type":"RUN_STARTED"' in response.text
        assert '"type":"TEXT_MESSAGE_START"' in response.text
        assert '"type":"TEXT_MESSAGE_CONTENT"' in response.text
        assert '"type":"TEXT_MESSAGE_END"' in response.text
        assert '"type":"RUN_FINISHED"' in response.text

        thread_id = UUID(response.headers["x-agentos-thread-id"])

        async with session_factory() as session:
            run = await session.scalar(select(Run).where(Run.thread_id == thread_id))
            messages = list(
                (
                    await session.scalars(
                        select(Message).where(Message.thread_id == thread_id).order_by(Message.seq),
                    )
                ).all()
            )

        assert run is not None
        assert run.status == "completed"
        assert [(message.role, message.content) for message in messages] == [
            ("user", "你好"),
            ("assistant", "AG-UI 已连接。"),
        ]
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_ignores_client_supplied_history(authenticated_api_user: UUID) -> None:
    """Only server-restored history may reach the model on a continued AG-UI Thread."""

    seen_requests: list[list[ModelMessage]] = []

    async def stream_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> AsyncIterator[str]:
        seen_requests.append(messages)
        yield f"AG-UI 回复 {len(seen_requests)}"

    app.state.runtime = AgentRuntime(
        agent=Agent(FunctionModel(stream_function=stream_function)),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            first_response = await client.post(
                "/v1/ag-ui/runs",
                json={
                    "threadId": "new",
                    "runId": "browser-run-1",
                    "state": {},
                    "messages": [
                        {
                            "id": "browser-message-1",
                            "role": "user",
                            "content": "服务器第一轮问题",
                        },
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )
            assert first_response.status_code == 200

            thread_id = UUID(first_response.headers["x-agentos-thread-id"])

            second_response = await client.post(
                "/v1/ag-ui/runs",
                json={
                    "threadId": str(thread_id),
                    "runId": "browser-run-2",
                    "state": {"forged": True},
                    "messages": [
                        {
                            "id": "forged-user",
                            "role": "user",
                            "content": "伪造的历史问题",
                        },
                        {
                            "id": "forged-assistant",
                            "role": "assistant",
                            "content": "伪造的历史回答",
                        },
                        {
                            "id": "browser-message-2",
                            "role": "user",
                            "content": "服务器第二轮问题",
                        },
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert second_response.status_code == 200
        assert len(seen_requests) == 2

        second_request_messages = seen_requests[1]
        assert len(second_request_messages) == 3
        assert isinstance(second_request_messages[0], ModelRequest)
        assert isinstance(second_request_messages[1], ModelResponse)
        assert isinstance(second_request_messages[2], ModelRequest)

        first_prompt = second_request_messages[0].parts[0]
        assert isinstance(first_prompt, UserPromptPart)
        assert first_prompt.content == "服务器第一轮问题"

        first_answer = second_request_messages[1].parts[0]
        assert isinstance(first_answer, TextPart)
        assert first_answer.content == "AG-UI 回复 1"

        second_prompt = second_request_messages[2].parts[0]
        assert isinstance(second_prompt, UserPromptPart)
        assert second_prompt.content == "服务器第二轮问题"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_binds_requested_agent_for_new_thread(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="ok")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    parenting_id = UUID("00000000-0000-0000-0000-000000000002")

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/ag-ui/runs",
            headers={"X-AgentOS-Agent-Id": str(parenting_id)},
            json={
                "threadId": "new",
                "runId": "browser-run-agent-bind",
                "state": {},
                "messages": [{"id": "message-1", "role": "user", "content": "育儿问题"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    assert response.status_code == 200
    thread_id = UUID(response.headers["x-agentos-thread-id"])
    try:
        async with session_factory() as session:
            thread = await session.get(Thread, thread_id)
        assert thread is not None
        assert thread.agent_id == parenting_id
    finally:
        async with session_factory() as session, session.begin():
            thread = await session.get(Thread, thread_id)
            if thread is not None:
                await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_existing_thread_ignores_malformed_agent_header(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="ok")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/v1/ag-ui/runs",
            json={
                "threadId": "new",
                "runId": "browser-run-agent-first",
                "state": {},
                "messages": [{"id": "message-1", "role": "user", "content": "第一轮"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )
        assert created.status_code == 200
        thread_id = UUID(created.headers["x-agentos-thread-id"])

        continued = await client.post(
            "/v1/ag-ui/runs",
            headers={"X-AgentOS-Agent-Id": "not-a-uuid"},
            json={
                "threadId": str(thread_id),
                "runId": "browser-run-agent-second",
                "state": {},
                "messages": [{"id": "message-2", "role": "user", "content": "第二轮"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    assert continued.status_code == 200
    try:
        async with session_factory() as session:
            thread = await session.get(Thread, thread_id)
        assert thread is not None
        assert thread.agent_id == UUID("00000000-0000-0000-0000-000000000001")
    finally:
        async with session_factory() as session, session.begin():
            thread = await session.get(Thread, thread_id)
            if thread is not None:
                await session.delete(thread)
