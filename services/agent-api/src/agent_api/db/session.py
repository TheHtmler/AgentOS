from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_api.config import get_settings

settings = get_settings()

# The engine is process-scoped; request code will obtain short-lived sessions from it.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)
session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Provide one transaction-capable session per future request handler."""

    async with session_factory() as session:
        yield session


async def close_database() -> None:
    """Release pooled PostgreSQL connections during application shutdown."""

    await engine.dispose()
