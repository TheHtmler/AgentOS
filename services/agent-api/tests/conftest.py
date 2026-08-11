from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.models import User
from agent_api.db.session import close_database, session_factory
from agent_api.main import app


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
