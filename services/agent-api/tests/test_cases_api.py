from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User
from agent_api.db.session import close_database, session_factory
from agent_api.main import app

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture(autouse=True)
async def dispose_database_pool():
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_cases_list_create_default_and_confirm(
    authenticated_api_user: UUID,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        listed = await client.get(f"/v1/cases?agent_id={IMD_AGENT_ID}")
        assert listed.status_code == 200
        assert listed.json()["cases"] == []

        created = await client.post(
            "/v1/cases",
            json={
                "agent_id": str(IMD_AGENT_ID),
                "display_name": "小明",
                "make_default": True,
            },
        )
        assert created.status_code == 200
        case_id = created.json()["id"]
        assert created.json()["display_name"] == "小明"
        assert created.json()["is_default"] is True

        second = await client.post(
            "/v1/cases",
            json={
                "agent_id": str(IMD_AGENT_ID),
                "display_name": "小红",
                "make_default": False,
            },
        )
        assert second.status_code == 200
        second_id = second.json()["id"]

        patched = await client.patch(
            f"/v1/cases/{second_id}/default",
            json={"agent_id": str(IMD_AGENT_ID)},
        )
        assert patched.status_code == 200
        assert patched.json()["is_default"] is True

        listed = await client.get(f"/v1/cases?agent_id={IMD_AGENT_ID}")
        assert listed.status_code == 200
        cases = listed.json()["cases"]
        assert len(cases) == 2
        assert sum(1 for item in cases if item["is_default"]) == 1

    async with session_factory() as session, session.begin():
        session.add(
            CaseFact(
                id=uuid4(),
                case_id=UUID(case_id),
                key="height_cm",
                content="身高 88 cm",
                tags=["身高"],
                status="proposed",
            ),
        )

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        facts = await client.get(f"/v1/cases/{case_id}/facts")
        assert facts.status_code == 200
        fact_id = facts.json()["facts"][0]["id"]
        confirmed = await client.post(f"/v1/cases/{case_id}/facts/{fact_id}/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"


@pytest.mark.anyio
async def test_cases_isolation(authenticated_api_user: UUID) -> None:
    other_id: UUID
    async with session_factory() as session, session.begin():
        other = User(email=f"case-iso-{uuid4().hex}@example.com", status="active")
        session.add(other)
        await session.flush()
        other_id = other.id
        foreign = await ensure_default_case(
            session,
            user_id=other_id,
            agent_id=IMD_AGENT_ID,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/v1/cases/{foreign}/facts")
        assert response.status_code == 404
