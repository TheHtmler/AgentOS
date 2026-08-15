"""Create empty Thread API for pre-chat uploads."""

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.db.models import Thread
from agent_api.db.session import close_database, session_factory
from agent_api.main import app


@pytest.fixture(autouse=True)
async def dispose_database_pool():
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_post_thread_creates_empty_conversation(authenticated_api_user: UUID) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/v1/threads", json={})

    assert response.status_code == 201
    body = response.json()
    thread_id = UUID(body["id"])
    assert body["agent_id"]
    assert body["title"] is None

    async with session_factory() as session:
        thread = await session.get(Thread, thread_id)
        assert thread is not None
        assert thread.user_id == authenticated_api_user
        assert thread.deleted_at is None
