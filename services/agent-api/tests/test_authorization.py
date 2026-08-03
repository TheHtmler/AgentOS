import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from agent_api.db.auth_store import (
    consume_auth_token,
    create_invited_user,
    create_user_session,
    issue_auth_token,
)
from agent_api.db.chat_store import start_run
from agent_api.db.models import User
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


async def active_session_token(email: str) -> tuple[UUID, str]:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = await create_invited_user(session, email=email)
        invitation = await issue_auth_token(
            session,
            user=user,
            purpose="invite",
            expires_at=now + timedelta(minutes=15),
            now=now,
        )
        active_user = await consume_auth_token(
            session,
            token=invitation.token,
            purpose="invite",
            now=now,
        )
        issued_session = await create_user_session(
            session,
            user=active_user,
            expires_at=now + timedelta(days=1),
            now=now,
        )

    return user.id, issued_session.token


@pytest.mark.anyio
async def test_chat_resources_require_a_session_and_are_owner_scoped() -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="私有回复。")),
        model_semaphore=asyncio.Semaphore(1),
    )
    first_user_id: UUID | None = None
    second_user_id: UUID | None = None

    try:
        first_user_id, first_session = await active_session_token("first-owner@example.com")
        second_user_id, second_session = await active_session_token("second-owner@example.com")
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as anonymous_client:
            anonymous_response = await anonymous_client.get("/v1/threads")

        async with AsyncClient(transport=transport, base_url="http://testserver") as first_client:
            first_client.cookies.set("agentos_session", first_session)
            chat_response = await first_client.post("/v1/chat/stream", json={"message": "仅我可见"})
            thread_id = UUID(chat_response.headers["x-agentos-thread-id"])
            run_id = UUID(chat_response.headers["x-agentos-run-id"])

        async with AsyncClient(transport=transport, base_url="http://testserver") as second_client:
            second_client.cookies.set("agentos_session", second_session)
            thread_response = await second_client.get(f"/v1/threads/{thread_id}/messages")
            run_response = await second_client.get(f"/v1/runs/{run_id}")
            list_response = await second_client.get("/v1/threads")

        assert anonymous_response.status_code == 401
        assert chat_response.status_code == 200
        assert thread_response.status_code == 404
        assert run_response.status_code == 404
        assert list_response.status_code == 200
        assert list_response.json() == {"threads": []}
    finally:
        for user_id in (first_user_id, second_user_id):
            if user_id is not None:
                async with session_factory() as session, session.begin():
                    user = await session.get(User, user_id)
                    if user is not None:
                        await session.delete(user)


@pytest.mark.anyio
async def test_cancel_run_requires_owner_and_persists_terminal_state(
    authenticated_api_user: UUID,
) -> None:
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel(custom_output_text="不会执行。")),
        model_semaphore=asyncio.Semaphore(1),
    )

    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="后台运行后显式停止",
            model_name="test-model",
            user_id=authenticated_api_user,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(f"/v1/runs/{started.run_id}/cancel")
        detail = await client.get(f"/v1/runs/{started.run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert detail.status_code == 200
    assert detail.json()["status"] == "cancelled"
