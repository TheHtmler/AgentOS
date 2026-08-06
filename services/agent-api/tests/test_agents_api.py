from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.db.models import Agent
from agent_api.db.session import close_database, session_factory
from agent_api.main import app


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_list_agents_returns_general_and_imd(
    authenticated_api_user: UUID,
) -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/agents")

    assert response.status_code == 200
    agents = response.json()["agents"]
    slugs = {agent["slug"] for agent in agents}
    assert {"general", "imd"} <= slugs
    assert "mma-pa" not in slugs
    assert "parenting" not in slugs
    assert len([agent for agent in agents if agent["is_default"]]) == 1
    imd = next(agent for agent in agents if agent["slug"] == "imd")
    assert imd["name"] == "遗传代谢"
    assert imd["case_enabled"] is True
    general = next(agent for agent in agents if agent["slug"] == "general")
    assert general["case_enabled"] is False


@pytest.mark.anyio
async def test_list_agents_skips_active_agent_without_published_version(
    authenticated_api_user: UUID,
) -> None:
    orphaned_agent_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Agent(
                id=orphaned_agent_id,
                slug=f"unpublished-{orphaned_agent_id.hex}",
                name="Unpublished",
                kind="general",
                status="active",
                is_default=False,
            ),
        )

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/v1/agents")
        assert response.status_code == 200
        assert str(orphaned_agent_id) not in {agent["id"] for agent in response.json()["agents"]}
    finally:
        async with session_factory() as session, session.begin():
            agent = await session.get(Agent, orphaned_agent_id)
            if agent is not None:
                await session.delete(agent)
