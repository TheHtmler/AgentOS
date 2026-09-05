import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

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

from agent_api.db.models import Message, Run, RunEvent, Thread
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime
from agent_api.tools.search.tool import AgentDeps
from agent_api.tools.util.tool import calculate


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


@pytest.mark.anyio
async def test_ag_ui_usage_limit_stops_a_runaway_tool_loop(
    authenticated_api_user: UUID,
) -> None:
    """A model stuck calling the same tool forever must be stopped, not run forever."""

    from pydantic_ai.models.function import DeltaToolCall

    call_count = 0

    async def echo_tool() -> str:
        nonlocal call_count
        call_count += 1
        return f"result {call_count}"

    async def stream_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name="echo_tool",
                json_args="{}",
                tool_call_id=f"call-{len(messages)}",
            ),
        }

    agent = Agent(FunctionModel(stream_function=stream_function))
    agent.tool_plain(echo_tool)

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
                    "runId": "browser-run-loop",
                    "state": {},
                    "messages": [
                        {"id": "browser-message-1", "role": "user", "content": "触发工具死循环"},
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 200
        assert '"type":"RUN_ERROR"' in response.text
        assert "步数过多" in response.text
        thread_id = UUID(response.headers["x-agentos-thread-id"])

        async with session_factory() as session:
            run = await session.scalar(select(Run).where(Run.thread_id == thread_id))
        assert run is not None
        assert run.status == "failed"
        assert run.error_message is not None
        assert "UsageLimitExceeded" in run.error_message
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_forces_emergency_notice_regardless_of_model_output(
    authenticated_api_user: UUID,
) -> None:
    """A red-flag user message must get the escalation notice even if the model ignores it."""

    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="孩子应该没什么大事，多喝水就行。")),
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
                    "runId": "browser-run-emergency",
                    "state": {},
                    "messages": [
                        {
                            "id": "browser-message-1",
                            "role": "user",
                            "content": "孩子突然抽搐了，一直叫不醒怎么办",
                        },
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 200
        body = response.text
        assert "检测到您描述的情况包含可能的紧急信号" in body
        # The forced notice must be the FIRST assistant text in the stream, not
        # tacked on after — and must not corrupt run framing.
        run_started_index = body.index('"type":"RUN_STARTED"')
        notice_index = body.index("检测到您描述的情况包含可能的紧急信号")
        model_text_index = body.index("多喝水就行")
        assert run_started_index < notice_index < model_text_index
        assert body.count('"type":"RUN_FINISHED"') == 1

        thread_id = UUID(response.headers["x-agentos-thread-id"])
        async with session_factory() as session:
            messages = list(
                (
                    await session.scalars(
                        select(Message).where(Message.thread_id == thread_id).order_by(Message.seq),
                    )
                ).all()
            )
        assistant_messages = [m for m in messages if m.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content.startswith("检测到您描述的情况包含可能的紧急信号")
        assert "多喝水就行" in assistant_messages[0].content
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_no_emergency_notice_for_routine_message(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="好的，记录下来了。")),
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
                    "runId": "browser-run-routine",
                    "state": {},
                    "messages": [
                        {"id": "browser-message-1", "role": "user", "content": "孩子有点咳嗽"},
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 200
        assert "检测到您描述的情况包含可能的紧急信号" not in response.text
        thread_id = UUID(response.headers["x-agentos-thread-id"])
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


def _sse_events(body: str) -> list[dict[str, object]]:
    """Parse `data:` frames of a buffered SSE body into ordered event dicts."""

    events: list[dict[str, object]] = []
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


@pytest.mark.anyio
async def test_ag_ui_streams_the_full_tool_call_event_sequence(
    authenticated_api_user: UUID,
) -> None:
    """TOOL_CALL_START/ARGS/END/RESULT stream in order, pair by toolCallId, and
    match the tool_call/tool_result rows persisted in run_events."""

    from pydantic_ai.models.function import DeltaToolCall

    invocation_count = 0

    async def stream_function(
        messages: list[ModelMessage],
        _: AgentInfo,
    ) -> AsyncIterator[dict[int, DeltaToolCall] | str]:
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            yield {
                0: DeltaToolCall(
                    name="calculate",
                    json_args='{"expression": "1+1"}',
                    tool_call_id="call-calc-1",
                ),
            }
        else:
            yield "计算结果是 2。"

    agent = Agent(FunctionModel(stream_function=stream_function), deps_type=AgentDeps)
    agent.tool(calculate)

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
                    "runId": "browser-run-tool-events",
                    "state": {},
                    "messages": [
                        {"id": "browser-message-1", "role": "user", "content": "算一下 1+1"},
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 200
        thread_id = UUID(response.headers["x-agentos-thread-id"])
        events = _sse_events(response.text)
        event_types = [event["type"] for event in events]

        start_index = event_types.index("TOOL_CALL_START")
        args_index = event_types.index("TOOL_CALL_ARGS")
        end_index = event_types.index("TOOL_CALL_END")
        result_index = event_types.index("TOOL_CALL_RESULT")
        finished_index = event_types.index("RUN_FINISHED")
        assert start_index < args_index < end_index < result_index < finished_index

        start_event = events[start_index]
        assert start_event["toolCallId"] == "call-calc-1"
        assert start_event["toolCallName"] == "calculate"

        args_text = "".join(
            str(event["delta"])
            for event in events
            if event["type"] == "TOOL_CALL_ARGS" and event["toolCallId"] == "call-calc-1"
        )
        assert args_text == '{"expression": "1+1"}'

        result_event = events[result_index]
        assert result_event["toolCallId"] == "call-calc-1"
        result_content = json.loads(str(result_event["content"]))
        assert result_content["ok"] is True
        assert result_content["result"] == 2

        async with session_factory() as session:
            run = await session.scalar(select(Run).where(Run.thread_id == thread_id))
            assert run is not None
            run_events = list(
                (
                    await session.scalars(
                        select(RunEvent)
                        .where(
                            RunEvent.run_id == run.id,
                            RunEvent.event_type.in_(["tool_call", "tool_result"]),
                        )
                        .order_by(RunEvent.seq),
                    )
                ).all()
            )

        assert run.status == "completed"
        assert [event.event_type for event in run_events] == ["tool_call", "tool_result"]
        assert run_events[0].payload["tool"] == "calculate"
        assert run_events[0].payload["args"] == {"expression": "1+1"}
        assert run_events[1].payload["tool"] == "calculate"
        assert run_events[1].payload["ok"] is True
        assert str(run_events[1].payload["summary"]).startswith("result=2")
        assert run_events[1].payload.get("duration_ms") is not None
    finally:
        if thread_id is not None:
            async with session_factory() as session, session.begin():
                thread = await session.get(Thread, thread_id)
                if thread is not None:
                    await session.delete(thread)


@pytest.mark.anyio
async def test_ag_ui_rejects_provider_without_tool_support(
    authenticated_api_user: UUID,
) -> None:
    """A provider flagged supports_tools=false fails the run fast with a 409,
    before any model call, instead of dying mid-stream on the endpoint's error."""

    from agent_api.db.models import AgentVersion, ModelProvider

    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="不应到达模型。")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    default_agent_id = UUID("00000000-0000-0000-0000-000000000001")
    provider_id = uuid4()
    original_provider_id: UUID | None = None

    async with session_factory() as session, session.begin():
        session.add(
            ModelProvider(
                id=provider_id,
                slug=f"no-tools-{provider_id.hex[:8]}",
                name="No tools fixture",
                base_url="https://api.example.com/v1",
                api_key=None,
                default_model="fixture-chat",
                api_mode="chat_completions",
                context_window=128_000,
                max_output_tokens=8_192,
                temperature=None,
                reasoning_summary=None,
                max_concurrent_runs=4,
                supports_vision=False,
                supports_tools=False,
                enabled=True,
            ),
        )
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == default_agent_id,
                AgentVersion.is_published.is_(True),
            ),
        )
        assert version is not None
        original_provider_id = version.model_provider_id
        version.model_provider_id = provider_id

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/ag-ui/runs",
                json={
                    "threadId": "new",
                    "runId": "browser-run-no-tools",
                    "state": {},
                    "messages": [
                        {"id": "browser-message-1", "role": "user", "content": "你好"},
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "The configured model does not support tool calls"

        async with session_factory() as session:
            thread = await session.scalar(
                select(Thread).where(Thread.user_id == authenticated_api_user),
            )
            assert thread is not None
            run = await session.scalar(select(Run).where(Run.thread_id == thread.id))
            assert run is not None
            assert run.status == "failed"

        async with session_factory() as session, session.begin():
            thread = await session.get(Thread, thread.id)
            if thread is not None:
                await session.delete(thread)
    finally:
        async with session_factory() as session, session.begin():
            version = await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.agent_id == default_agent_id,
                    AgentVersion.is_published.is_(True),
                ),
            )
            if version is not None:
                version.model_provider_id = original_provider_id
            provider = await session.get(ModelProvider, provider_id)
            if provider is not None:
                await session.delete(provider)
