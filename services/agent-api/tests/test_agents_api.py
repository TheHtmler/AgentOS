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
async def test_list_agents_returns_general_and_parenting(
    authenticated_api_user: UUID,
) -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/agents")

    assert response.status_code == 200
    agents = response.json()["agents"]
    assert {"general", "parenting"} <= {agent["slug"] for agent in agents}
    assert len([agent for agent in agents if agent["is_default"]]) == 1
