from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from agent_api.api.auth import get_current_user
from agent_api.db.models import User
from agent_api.db.session import session_factory
from agent_api.main import app


@pytest.fixture
async def authenticated_api_user() -> AsyncIterator[UUID]:
    """Provide a persisted identity for protected API tests without bypassing production code."""

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
