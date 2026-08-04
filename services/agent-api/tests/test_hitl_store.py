"""HITL interrupt persistence and run pause helpers."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.config import get_settings
from agent_api.db.chat_store import start_run
from agent_api.db.models import Interrupt, Run


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
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


@pytest.mark.anyio
async def test_interrupt_row_roundtrip(database_session: AsyncSession) -> None:
    session = database_session
    transaction = await session.begin()

    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="需要审批的抓取",
            model_name="gemma4:e4b",
        )
        run = await session.get(Run, started.run_id)
        assert run is not None
        run.status = "waiting_approval"

        interrupt = Interrupt(
            id=uuid4(),
            run_id=started.run_id,
            tool_call_id="call_fetch_1",
            tool_name="fetch_url",
            tool_args={"url": "https://example.com"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        session.add(interrupt)
        await session.flush()

        loaded = await session.get(Interrupt, interrupt.id)
        assert loaded is not None
        assert loaded.tool_name == "fetch_url"
        assert loaded.status == "pending"
        assert loaded.tool_args["url"] == "https://example.com"

        refreshed = await session.get(Run, started.run_id)
        assert refreshed is not None
        assert refreshed.status == "waiting_approval"
    finally:
        await transaction.rollback()
