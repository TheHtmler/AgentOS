from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.api import auth as auth_api
from agent_api.config import get_settings
from agent_api.db.auth_store import (
    consume_auth_token,
    create_invited_user,
    create_user_session,
    find_user_by_email,
    issue_auth_token,
)
from agent_api.db.models import User
from agent_api.db.session import close_database, session_factory
from agent_api.main import app


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_auth_token_creates_session_and_logout_revokes_it() -> None:
    user_id: UUID | None = None
    now = datetime.now(UTC)

    try:
        async with session_factory() as session, session.begin():
            user = await create_invited_user(
                session,
                email=f"auth-{uuid4().hex}@example.com",
            )
            user_id = user.id
            invitation = await issue_auth_token(
                session,
                user=user,
                purpose="invite",
                expires_at=now + timedelta(minutes=15),
                now=now,
            )

        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            verify_response = await client.post(
                "/v1/auth/verify",
                json={
                    "token": invitation.token,
                    "purpose": "invite",
                },
            )

            assert verify_response.status_code == 200
            session_token = verify_response.json()["session_token"]
            client.cookies.set("agentos_session", session_token)

            me_response = await client.get("/v1/auth/me")
            logout_response = await client.post("/v1/auth/logout")
            revoked_me_response = await client.get("/v1/auth/me")

        assert me_response.status_code == 200
        assert me_response.json()["id"] == str(user_id)
        assert logout_response.status_code == 204
        assert revoked_me_response.status_code == 401
    finally:
        if user_id is not None:
            async with session_factory() as session, session.begin():
                user = await session.get(User, user_id)

                if user is not None:
                    await session.delete(user)


@pytest.mark.anyio
async def test_only_configured_admin_can_create_an_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_user_id: UUID | None = None
    non_admin_user_id: UUID | None = None
    invited_user_id: UUID | None = None
    now = datetime.now(UTC)
    settings = get_settings().model_copy(
        update={
            "auth_admin_emails": "admin@example.com",
            "web_app_origin": "http://web.test",
        }
    )
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)

    try:
        async with session_factory() as session, session.begin():
            admin = await create_invited_user(session, email="admin@example.com")
            admin_user_id = admin.id
            admin_invitation = await issue_auth_token(
                session,
                user=admin,
                purpose="invite",
                expires_at=now + timedelta(minutes=15),
                now=now,
            )
            active_admin = await consume_auth_token(
                session,
                token=admin_invitation.token,
                purpose="invite",
                now=now,
            )
            admin_session = await create_user_session(
                session,
                user=active_admin,
                expires_at=now + timedelta(days=1),
                now=now,
            )
            non_admin = await create_invited_user(session, email="member-manager@example.com")
            non_admin_user_id = non_admin.id
            non_admin_invitation = await issue_auth_token(
                session,
                user=non_admin,
                purpose="invite",
                expires_at=now + timedelta(minutes=15),
                now=now,
            )
            active_non_admin = await consume_auth_token(
                session,
                token=non_admin_invitation.token,
                purpose="invite",
                now=now,
            )
            non_admin_session = await create_user_session(
                session,
                user=active_non_admin,
                expires_at=now + timedelta(days=1),
                now=now,
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthenticated_response = await client.post(
                "/v1/auth/invitations",
                json={"email": "member@example.com"},
            )
            client.cookies.set("agentos_session", non_admin_session.token)
            non_admin_response = await client.post(
                "/v1/auth/invitations",
                json={"email": "member@example.com"},
            )
            client.cookies.set("agentos_session", admin_session.token)
            invitation_response = await client.post(
                "/v1/auth/invitations",
                json={"email": "member@example.com"},
            )

        assert unauthenticated_response.status_code == 401
        assert non_admin_response.status_code == 403
        assert invitation_response.status_code == 201
        invitation_payload = invitation_response.json()
        assert invitation_payload["email"] == "member@example.com"
        assert invitation_payload["invitation_url"].startswith("http://web.test/register?token=")

        async with session_factory() as session:
            invited_user = await find_user_by_email(session, email="member@example.com")
            assert invited_user is not None
            invited_user_id = invited_user.id
            assert invited_user.status == "invited"
    finally:
        for user_id in (admin_user_id, non_admin_user_id, invited_user_id):
            if user_id is not None:
                async with session_factory() as session, session.begin():
                    user = await session.get(User, user_id)
                    if user is not None:
                        await session.delete(user)
