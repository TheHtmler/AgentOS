from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_api.config import get_settings
from agent_api.db.auth_store import (
    InvalidAuthTokenError,
    InvalidUserSessionError,
    consume_auth_token,
    create_invited_user,
    create_user_session,
    get_active_user_for_session,
    issue_auth_token,
    revoke_user_session,
)


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    """Create an event-loop-local database connection for each AnyIO test."""

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
async def test_invitation_token_activates_once_and_session_can_be_revoked(
    database_session: AsyncSession,
) -> None:
    session = database_session
    transaction = await session.begin()
    now = datetime(2026, 8, 2, tzinfo=UTC)

    try:
        user = await create_invited_user(
            session,
            email="  Randy@example.com ",
        )
        invitation = await issue_auth_token(
            session,
            user=user,
            purpose="invite",
            expires_at=now + timedelta(minutes=15),
            now=now,
        )

        active_user = await consume_auth_token(
            session,
            token=invitation.token,
            purpose="invite",
            now=now,
        )
        session_token = await create_user_session(
            session,
            user=active_user,
            expires_at=now + timedelta(days=30),
            now=now,
        )
        resolved_user = await get_active_user_for_session(
            session,
            token=session_token.token,
            now=now,
        )

        assert user.email == "randy@example.com"
        assert active_user.status == "active"
        assert resolved_user.id == user.id

        with pytest.raises(InvalidAuthTokenError):
            await consume_auth_token(
                session,
                token=invitation.token,
                purpose="invite",
                now=now,
            )

        await revoke_user_session(
            session,
            token=session_token.token,
            now=now,
        )

        with pytest.raises(InvalidUserSessionError):
            await get_active_user_for_session(
                session,
                token=session_token.token,
                now=now,
            )
    finally:
        await transaction.rollback()
