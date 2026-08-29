"""Tests for the authenticated user's channel pairing controls."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.db.session import close_database
from agent_api.main import app


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_authenticated_user_can_issue_pairing_code(
    authenticated_api_user: UUID,
) -> None:
    assert authenticated_api_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        empty = await client.get("/v1/channel-bindings")
        assert empty.status_code == 200
        assert empty.json() == {"bindings": []}

        issued = await client.post("/v1/channel-bindings/pairing-codes")
        assert issued.status_code == 200
        body = issued.json()
        assert body["channel"] == "openclaw-weixin"
        assert len(body["code"]) == 8
        assert body["expires_at"]
