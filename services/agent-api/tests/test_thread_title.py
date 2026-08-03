import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.config import Settings, get_settings
from agent_api.db.chat_store import start_run, try_set_thread_title_if_empty
from agent_api.db.models import Thread
from agent_api.thread_title import normalize_title, schedule_auto_thread_title


def test_normalize_title_strips_quotes_and_extra_lines() -> None:
    assert normalize_title('  "天气查询"\n更多说明  ') == "天气查询"


def test_normalize_title_rejects_blank() -> None:
    assert normalize_title("   ") is None


def test_normalize_title_truncates() -> None:
    long = "字" * 100
    result = normalize_title(long)
    assert result is not None
    assert len(result) == 80


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_try_set_thread_title_if_empty_writes_once(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()
    try:
        started = await start_run(
            session,
            thread_id=None,
            user_content="帮我查天气",
            model_name="test-model",
        )
        first = await try_set_thread_title_if_empty(
            session,
            thread_id=started.thread_id,
            title="天气查询",
        )
        second = await try_set_thread_title_if_empty(
            session,
            thread_id=started.thread_id,
            title="另一个标题",
        )
        thread = await session.get(Thread, started.thread_id)
        assert first is True
        assert second is False
        assert thread is not None
        assert thread.title == "天气查询"
    finally:
        await transaction.rollback()


@pytest.mark.anyio
async def test_schedule_writes_when_title_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "database_url": get_settings().database_url,
            "auto_thread_title_enabled": True,
            "auto_thread_title_timeout_seconds": 5,
        },
    )
    monkeypatch.setattr("agent_api.thread_title.get_settings", lambda: settings)

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            started = await start_run(
                session,
                thread_id=None,
                user_content="帮我查天气",
                model_name="test-model",
            )
            thread_id = started.thread_id

        async def fake_generate(
            user_message: str,
            assistant_content: str,
            http_client: httpx.AsyncClient,
        ) -> str | None:
            return "天气查询"

        semaphore = asyncio.Semaphore(1)
        async with httpx.AsyncClient() as client:
            schedule_auto_thread_title(
                thread_id=thread_id,
                user_message="帮我查天气",
                assistant_content="今天晴。",
                model_semaphore=semaphore,
                http_client=client,
                generate_title=fake_generate,
            )
            for _ in range(50):
                await asyncio.sleep(0.05)
                async with factory() as session:
                    thread = await session.get(Thread, thread_id)
                    if thread is not None and thread.title == "天气查询":
                        break
            else:
                pytest.fail("auto title was not persisted")

        async with factory() as session, session.begin():
            thread = await session.get(Thread, thread_id)
            if thread is not None:
                await session.delete(thread)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_schedule_skips_when_title_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "database_url": get_settings().database_url,
            "auto_thread_title_enabled": True,
            "auto_thread_title_timeout_seconds": 5,
        },
    )
    monkeypatch.setattr("agent_api.thread_title.get_settings", lambda: settings)

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            started = await start_run(
                session,
                thread_id=None,
                user_content="已有标题",
                model_name="test-model",
            )
            thread = await session.get(Thread, started.thread_id)
            assert thread is not None
            thread.title = "手动标题"
            thread_id = started.thread_id

        called = False

        async def boom_generate(
            user_message: str,
            assistant_content: str,
            http_client: httpx.AsyncClient,
        ) -> str | None:
            nonlocal called
            called = True
            return "不应写入"

        semaphore = asyncio.Semaphore(1)
        async with httpx.AsyncClient() as client:
            schedule_auto_thread_title(
                thread_id=thread_id,
                user_message="已有标题",
                assistant_content="好的",
                model_semaphore=semaphore,
                http_client=client,
                generate_title=boom_generate,
            )
            await asyncio.sleep(0.3)

        assert called is False

        async with factory() as session, session.begin():
            thread = await session.get(Thread, thread_id)
            assert thread is not None
            assert thread.title == "手动标题"
            await session.delete(thread)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_schedule_disabled_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            "auto_thread_title_enabled": False,
        },
    )
    monkeypatch.setattr("agent_api.thread_title.get_settings", lambda: settings)

    called = False

    async def boom_generate(
        user_message: str,
        assistant_content: str,
        http_client: httpx.AsyncClient,
    ) -> str | None:
        nonlocal called
        called = True
        return "x"

    semaphore = asyncio.Semaphore(1)
    async with httpx.AsyncClient() as client:
        schedule_auto_thread_title(
            thread_id=uuid4(),
            user_message="hello",
            assistant_content="world",
            model_semaphore=semaphore,
            http_client=client,
            generate_title=boom_generate,
        )
        await asyncio.sleep(0.05)

    assert called is False
