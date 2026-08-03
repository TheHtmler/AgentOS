import asyncio
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from agent_api.api.chat import (
    chunk_assistant_text,
    load_thread_model_history,
    model_history_from_thread_messages,
    strip_thinking_parts,
)
from agent_api.db.chat_store import cancel_run, complete_run, start_run
from agent_api.db.models import Message, Run, RunEvent, RunMessageHistory, Thread
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    """Prevent asyncpg pooled connections from crossing pytest event loops."""

    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_chat_stream_persists_messages_and_events(authenticated_api_user: UUID) -> None:
    """Verify the HTTP stream and database facts describe the same completed Run."""

    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="AgentOS 已连接。")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/v1/chat/stream", json={"message": "你好"})

        assert response.status_code == 200
        assert "x-agentos-thread-id" in response.headers
        assert "x-agentos-run-id" in response.headers
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: text_delta\ndata:" in response.text
        assert "AgentOS" in response.text
        assert response.text.endswith("event: done\ndata: {}\n\n")

        thread_id = UUID(response.headers["x-agentos-thread-id"])

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            history_response = await client.get(f"/v1/threads/{thread_id}/messages")

        assert history_response.status_code == 200
        assert history_response.json()["thread_id"] == str(thread_id)
        assert [message["role"] for message in history_response.json()["messages"]] == [
            "user",
            "assistant",
        ]
        assert [message["content"] for message in history_response.json()["messages"]] == [
            "你好",
            "AgentOS 已连接。",
        ]
        assert history_response.json()["tool_calls"] == []

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            not_found_response = await client.get(f"/v1/threads/{uuid4()}/messages")

        assert not_found_response.status_code == 404

        async with session_factory() as session:
            messages = list(
                (
                    await session.scalars(
                        select(Message).where(Message.thread_id == thread_id).order_by(Message.seq),
                    )
                ).all()
            )
            run = await session.scalar(select(Run).where(Run.thread_id == thread_id))

            assert run is not None
            model_history = await session.get(RunMessageHistory, run.id)

            events = list(
                (
                    await session.scalars(
                        select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq),
                    )
                ).all()
            )

        assert [(message.seq, message.role) for message in messages] == [
            (1, "user"),
            (2, "assistant"),
        ]
        assert run.status == "completed"
        assert run.input_tokens is not None
        assert run.input_tokens > 0
        assert run.output_tokens is not None
        assert run.output_tokens > 0
        assert run.model_request_count == 1
        assert events[0].event_type == "run_started"
        assert events[-1].event_type == "run_completed"
        assert model_history is not None
        assert [message["kind"] for message in model_history.messages] == [
            "request",
            "response",
        ]
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            run_response = await client.get(f"/v1/runs/{run.id}")
            missing_run_response = await client.get(f"/v1/runs/{uuid4()}")

        assert run_response.status_code == 200
        assert missing_run_response.status_code == 404
        assert missing_run_response.json() == {"detail": "Run not found"}

        run_payload = run_response.json()
        assert run_payload["id"] == str(run.id)
        assert run_payload["thread_id"] == str(thread_id)
        assert run_payload["status"] == "completed"
        assert run_payload["input_tokens"] == run.input_tokens
        assert run_payload["output_tokens"] == run.output_tokens
        assert run_payload["model_request_count"] == 1
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_chat_stream_rejects_blank_message(authenticated_api_user: UUID) -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/v1/chat/stream", json={"message": "   "})

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.anyio
async def test_model_history_from_thread_messages_pairs_turns() -> None:
    rows = [
        Message(role="user", content="第一问", seq=1),
        Message(role="assistant", content="第一答", seq=2),
        Message(role="user", content="第二问", seq=3),
        Message(role="assistant", content="第二答", seq=4),
    ]
    history = model_history_from_thread_messages(rows)
    assert len(history) == 4
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], UserPromptPart)
    assert history[0].parts[0].content == "第一问"
    assert isinstance(history[1], ModelResponse)
    assert isinstance(history[1].parts[0], TextPart)
    assert history[1].parts[0].content == "第一答"


@pytest.mark.anyio
async def test_load_thread_model_history_falls_back_to_messages(
    authenticated_api_user: UUID,
) -> None:
    """When run_message_histories are absent, rebuild context from Message rows."""

    async with session_factory() as session, session.begin():
        first = await start_run(
            session,
            thread_id=None,
            user_content="历史问题",
            model_name="gemma4:e4b",
            user_id=authenticated_api_user,
        )
        await complete_run(
            session,
            run_id=first.run_id,
            assistant_content="历史回答",
            model_messages=None,
        )
        second = await start_run(
            session,
            thread_id=first.thread_id,
            user_content="当前问题",
            model_name="gemma4:e4b",
            user_id=authenticated_api_user,
        )
        thread_id = first.thread_id
        current_run_id = second.run_id

    try:
        history = await load_thread_model_history(thread_id, user_id=authenticated_api_user)
        assert len(history) == 2
        assert isinstance(history[0].parts[0], UserPromptPart)
        assert history[0].parts[0].content == "历史问题"
        assert isinstance(history[1].parts[0], TextPart)
        assert history[1].parts[0].content == "历史回答"
    finally:
        async with session_factory() as session, session.begin():
            await cancel_run(session, run_id=current_run_id)
            thread = await session.get(Thread, thread_id)
            if thread is not None:
                await session.delete(thread)


@pytest.mark.anyio
async def test_chat_stream_reuses_server_model_history(authenticated_api_user: UUID) -> None:
    """The second HTTP request must receive the first completed Run as model history."""

    seen_requests: list[list[ModelMessage]] = []

    def model_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> ModelResponse:
        # chat stream uses agent.run(), which requires a non-stream FunctionModel.
        seen_requests.append(messages)
        return ModelResponse(parts=[TextPart(content=f"回复 {len(seen_requests)}")])

    app.state.runtime = AgentRuntime(
        agent=Agent(FunctionModel(model_function)),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            first_response = await client.post(
                "/v1/chat/stream",
                json={"message": "第一轮问题"},
            )

            assert first_response.status_code == 200
            thread_id = UUID(first_response.headers["x-agentos-thread-id"])

            second_response = await client.post(
                "/v1/chat/stream",
                json={
                    "message": "第二轮问题",
                    "thread_id": str(thread_id),
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
        assert first_prompt.content == "第一轮问题"

        first_answer = second_request_messages[1].parts[0]
        assert isinstance(first_answer, TextPart)
        assert first_answer.content == "回复 1"

        second_prompt = second_request_messages[2].parts[0]
        assert isinstance(second_prompt, UserPromptPart)
        assert second_prompt.content == "第二轮问题"
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_chat_stream_rejects_second_running_run(authenticated_api_user: UUID) -> None:
    """A Thread may not start a second Run until the active Run reaches a terminal state."""

    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="恢复后可继续。")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None
    active_run_id: UUID | None = None

    try:
        async with session_factory() as session, session.begin():
            started = await start_run(
                session,
                thread_id=None,
                user_content="正在生成的第一条消息",
                model_name="test",
                user_id=authenticated_api_user,
            )
            thread_id = started.thread_id
            active_run_id = started.run_id

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            blocked_response = await client.post(
                "/v1/chat/stream",
                json={
                    "message": "不应启动的第二条消息",
                    "thread_id": str(thread_id),
                },
            )

        assert blocked_response.status_code == 409
        assert blocked_response.json() == {"detail": "Thread is already running"}

        async with session_factory() as session, session.begin():
            await cancel_run(session, run_id=active_run_id)

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resumed_response = await client.post(
                "/v1/chat/stream",
                json={
                    "message": "取消后允许发送的消息",
                    "thread_id": str(thread_id),
                },
            )

        assert resumed_response.status_code == 200
        assert "event: text_delta\ndata:" in resumed_response.text
        assert resumed_response.text.endswith("event: done\ndata: {}\n\n")
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_chunk_assistant_text_splits_completed_output() -> None:
    assert chunk_assistant_text("abcdefgh", chunk_size=3) == ["abc", "def", "gh"]
    assert chunk_assistant_text("") == []


@pytest.mark.anyio
async def test_chat_stream_returns_answer_after_tool_call(
    authenticated_api_user: UUID,
) -> None:
    """Tool loops must finish before SSE text is emitted as the final answer."""

    def lookup_rate(ctx: RunContext[None], query: str) -> str:
        return "USD/CNY=7.12"

    def model_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> ModelResponse:
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        return ModelResponse(
                            parts=[TextPart(content=f"今日汇率约为 {part.content}")]
                        )
        return ModelResponse(
            parts=[ToolCallPart(tool_name="lookup_rate", args={"query": "usd cny"})]
        )

    app.state.runtime = AgentRuntime(
        agent=Agent(FunctionModel(model_function), tools=[lookup_rate]),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    thread_id: UUID | None = None

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/chat/stream",
                json={"message": "查一下今天的人民币和美元的汇率"},
            )

        assert response.status_code == 200
        assert "今日汇率约为 USD/CNY=7.12" in response.text
        assert response.text.endswith("event: done\ndata: {}\n\n")
        thread_id = UUID(response.headers["x-agentos-thread-id"])

        async with session_factory() as session:
            run = await session.scalar(select(Run).where(Run.thread_id == thread_id))
            assert run is not None
            assert run.status == "completed"
            assert run.model_request_count == 2
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_strip_thinking_parts_preserves_final_answer() -> None:
    messages = [
        {
            "kind": "response",
            "parts": [
                {"part_kind": "thinking", "content": "private reasoning"},
                {"part_kind": "text", "content": "最终回答"},
            ],
        },
    ]

    sanitized = strip_thinking_parts(cast(list[dict[str, object]], messages))
    assert sanitized == [
        {
            "kind": "response",
            "parts": [{"part_kind": "text", "content": "最终回答"}],
        },
    ]