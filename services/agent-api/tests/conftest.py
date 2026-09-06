import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.base import Base
from agent_api.db.models import Agent, AgentVersion, ModelProvider, User
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from scripts.seed_agents import SEED_AGENTS

TEST_PROVIDER_ID = UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture(autouse=True)
def isolated_database_records(request: pytest.FixtureRequest) -> None:
    if not (
        {"database_session", "authenticated_api_user"} & set(request.fixturenames)
        or hasattr(getattr(request, "module", None), "session_factory")
    ):
        return

    async def reset() -> None:
        url = make_url(get_settings().database_url)
        if not url.database or not url.database.endswith("_test"):
            raise RuntimeError("Refusing to reset a non-test database")
        async with session_factory() as session, session.begin():
            tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
            await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            session.add(
                ModelProvider(
                    id=TEST_PROVIDER_ID,
                    slug="test-fixture",
                    name="Test fixture",
                    base_url="https://model.invalid/v1",
                    default_model="test",
                    context_window=32768,
                    max_output_tokens=4096,
                    max_concurrent_runs=1,
                    supports_vision=True,
                )
            )
            await session.flush()
            for spec in SEED_AGENTS:
                fields = asdict(spec)
                fields.pop("published_version")
                session.add(Agent(**fields))
                await session.flush()
                session.add(
                    AgentVersion(
                        agent_id=spec.id,
                        model_provider_id=TEST_PROVIDER_ID,
                        is_published=True,
                        **asdict(spec.published_version),
                    )
                )

    asyncio.run(reset())


async def create_run_via_ag_ui(
    client: AsyncClient,
    text: str,
    *,
    thread_id: UUID | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Create or continue a thread through the product AG-UI path (test setup helper)."""

    return await client.post(
        "/v1/ag-ui/runs",
        headers=headers,
        json={
            "threadId": str(thread_id) if thread_id is not None else "new",
            "runId": f"setup-run-{uuid4().hex[:8]}",
            "state": {},
            "messages": [{"id": f"setup-msg-{uuid4().hex[:8]}", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    """Create an event-loop-local database connection for each AnyIO test."""

    # asyncpg connections cannot be reused across pytest's separate event loops.
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def authenticated_api_user() -> AsyncIterator[UUID]:
    """Provide a persisted identity for protected API tests without bypassing production code."""

    # Reset the process-scoped pool before each AnyIO loop uses the fixture.
    await close_database()
    async with session_factory() as session, session.begin():
        user = User(email=f"test-user-{uuid4().hex}@example.com", status="active")
        session.add(user)
        await session.flush()
        user_id = user.id

    app.dependency_overrides[get_current_user] = lambda: user

    try:
        yield user_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)

        async with session_factory() as session, session.begin():
            persisted_user = await session.get(User, user_id)
            if persisted_user is not None:
                await session.delete(persisted_user)
        await close_database()
