import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.main import app


@pytest.mark.anyio
async def test_health() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
