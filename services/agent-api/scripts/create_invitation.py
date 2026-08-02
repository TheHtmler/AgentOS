"""Generate the initial, manually distributed invite URL from the trusted backend environment."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from agent_api.config import get_settings
from agent_api.db.auth_store import create_invited_user, find_user_by_email, issue_auth_token
from agent_api.db.session import close_database, session_factory


async def create_invitation(email: str) -> str:
    """Create or replace a pending invitation without exposing an unauthenticated HTTP endpoint."""

    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.auth_invite_ttl_minutes)

    async with session_factory() as session, session.begin():
        user = await find_user_by_email(session, email=email)
        if user is None:
            user = await create_invited_user(session, email=email)
        elif user.status != "invited":
            raise ValueError("an active or disabled user already uses this email")

        invitation = await issue_auth_token(
            session,
            user=user,
            purpose="invite",
            expires_at=expires_at,
            now=now,
        )

    return f"{settings.web_app_origin}/register?{urlencode({'token': invitation.token})}"


async def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: uv run --directory services/agent-api python scripts/create_invitation.py EMAIL"
        )
        return 2

    try:
        invitation_url = await create_invitation(sys.argv[1])
    except ValueError as error:
        print(f"Unable to create invitation: {error}", file=sys.stderr)
        return 1
    finally:
        await close_database()

    print(invitation_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
