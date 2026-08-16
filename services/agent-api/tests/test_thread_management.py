import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from conftest import create_run_via_ag_ui
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from agent_api.db.chat_store import list_threads, soft_delete_thread, start_run
from agent_api.db.models import Thread
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
async def test_rename_and_soft_delete_thread_via_api(authenticated_api_user: UUID) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="ok")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await create_run_via_ag_ui(client, "需要重命名的会话")
        assert chat_response.status_code == 200
        thread_id = UUID(chat_response.headers["x-agentos-thread-id"])

        rename_response = await client.patch(
            f"/v1/threads/{thread_id}",
            json={"title": "  我的计划  "},
        )
        assert rename_response.status_code == 200
        assert rename_response.json()["title"] == "我的计划"

        list_response = await client.get("/v1/threads")
        assert list_response.status_code == 200
        assert any(
            item["id"] == str(thread_id) and item["title"] == "我的计划"
            for item in list_response.json()["threads"]
        )

        clear_response = await client.patch(f"/v1/threads/{thread_id}", json={"title": "   "})
        assert clear_response.status_code == 200
        assert clear_response.json()["title"] is None

        too_long = await client.patch(
            f"/v1/threads/{thread_id}",
            json={"title": "x" * 81},
        )
        assert too_long.status_code == 422

        delete_response = await client.delete(f"/v1/threads/{thread_id}")
        assert delete_response.status_code == 204

        idempotent = await client.delete(f"/v1/threads/{thread_id}")
        assert idempotent.status_code == 204

        missing_messages = await client.get(f"/v1/threads/{thread_id}/messages")
        assert missing_messages.status_code == 404

        listed = await client.get("/v1/threads")
        assert all(item["id"] != str(thread_id) for item in listed.json()["threads"])

        continue_chat = await create_run_via_ag_ui(client, "续聊", thread_id=thread_id)
        assert continue_chat.status_code == 404


@pytest.mark.anyio
async def test_soft_delete_hides_thread_from_list_store(
    authenticated_api_user: UUID,
) -> None:
    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="将被软删",
            model_name="gemma4:e4b",
            user_id=authenticated_api_user,
        )
        thread_id = started.thread_id

    async with session_factory() as session, session.begin():
        deleted = await soft_delete_thread(
            session,
            thread_id=thread_id,
            user_id=authenticated_api_user,
        )
        assert deleted is True
        threads = await list_threads(session, limit=20, user_id=authenticated_api_user)
        assert all(item.id != thread_id for item in threads)

        thread = await session.get(Thread, thread_id)
        assert thread is not None
        assert thread.deleted_at is not None


@pytest.mark.anyio
async def test_chat_binds_requested_agent_for_new_thread(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="ok")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)
    parenting_id = UUID("00000000-0000-0000-0000-000000000002")

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await create_run_via_ag_ui(
            client,
            "育儿问题",
            headers={"X-AgentOS-Agent-Id": str(parenting_id)},
        )
        assert response.status_code == 200
        thread_id = UUID(response.headers["x-agentos-thread-id"])
        history_response = await client.get(f"/v1/threads/{thread_id}/messages")

    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert history_payload["agent_id"] == str(parenting_id)
    assert history_payload["latest_run"] is not None
    assert history_payload["latest_run"]["status"] == "completed"
    assert isinstance(history_payload["latest_run"]["id"], str)
    async with session_factory() as session:
        thread = await session.get(Thread, thread_id)
    assert thread is not None
    assert thread.agent_id == parenting_id


@pytest.mark.anyio
async def test_existing_chat_ignores_malformed_agent_header(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="ok")),
        model_semaphore=asyncio.Semaphore(1),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await create_run_via_ag_ui(client, "第一轮")
        assert created.status_code == 200
        thread_id = UUID(created.headers["x-agentos-thread-id"])

        continued = await create_run_via_ag_ui(
            client,
            "第二轮",
            thread_id=thread_id,
            headers={"X-AgentOS-Agent-Id": "not-a-uuid"},
        )

    assert continued.status_code == 200
    async with session_factory() as session:
        thread = await session.get(Thread, thread_id)
    assert thread is not None
    assert thread.agent_id == UUID("00000000-0000-0000-0000-000000000001")
