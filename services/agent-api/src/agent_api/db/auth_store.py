import secrets
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import AuthToken, User, UserSession

type AuthTokenPurpose = Literal["invite", "magic_link"]

PASSWORD_HASHER = PasswordHash.recommended()


class UserAlreadyExistsError(ValueError):
    """Raised when an invitation reuses an existing email address."""


class InvalidAuthTokenError(ValueError):
    """Raised when a token is missing, expired, consumed, or has the wrong purpose."""


class InvalidUserSessionError(ValueError):
    """Raised when an opaque browser session is absent, expired, or revoked."""


class InvalidCredentialsError(ValueError):
    """Raised for every failed login so callers do not disclose account existence."""


@dataclass(frozen=True)
class IssuedAuthToken:
    """The plaintext token is returned once for delivery and never persisted."""

    user_id: UUID
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedUserSession:
    """The plaintext session token is returned once for an HttpOnly Cookie."""

    user_id: UUID
    token: str
    expires_at: datetime


def normalize_email(email: str) -> str:
    """Validate and normalize an email address without DNS deliverability checks."""

    try:
        validated = validate_email(
            email.strip(),
            check_deliverability=False,
        )
    except EmailNotValidError as error:
        raise ValueError("email must be a valid address") from error

    # AgentOS treats email addresses as case-insensitive account identifiers.
    return validated.normalized.casefold()


def hash_secret(secret: str) -> str:
    """Hash high-entropy opaque tokens before persisting them."""

    return sha256(secret.encode("utf-8")).hexdigest()


def validate_new_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    if len(password) > 128:
        raise ValueError("password must contain at most 128 characters")


async def create_invited_user(
    session: AsyncSession,
    *,
    email: str,
) -> User:
    """Create an invite-only account while relying on the unique DB constraint."""

    normalized_email = normalize_email(email)
    existing_user = await session.scalar(select(User).where(User.email == normalized_email))

    if existing_user is not None:
        raise UserAlreadyExistsError(f"User {normalized_email} already exists")

    user = User(email=normalized_email)
    session.add(user)
    await session.flush()
    return user


async def find_user_by_email(
    session: AsyncSession,
    *,
    email: str,
) -> User | None:
    """Look up an account without exposing this operation through a public API."""

    return await session.scalar(select(User).where(User.email == normalize_email(email)))


async def issue_auth_token(
    session: AsyncSession,
    *,
    user: User,
    purpose: AuthTokenPurpose,
    expires_at: datetime,
    now: datetime,
) -> IssuedAuthToken:
    """Issue one token and invalidate older unused tokens of the same purpose."""

    if expires_at <= now:
        raise ValueError("expires_at must be in the future")

    await session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
        )
        .values(consumed_at=now),
    )

    token = secrets.token_urlsafe(32)
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_secret(token),
            expires_at=expires_at,
        ),
    )
    await session.flush()

    return IssuedAuthToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )


async def consume_auth_token(
    session: AsyncSession,
    *,
    token: str,
    purpose: AuthTokenPurpose,
    now: datetime,
) -> User:
    """Consume one valid token and activate its invited account atomically."""

    auth_token = await session.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_secret(token)).with_for_update(),
    )

    if (
        auth_token is None
        or auth_token.purpose != purpose
        or auth_token.consumed_at is not None
        or auth_token.expires_at <= now
    ):
        raise InvalidAuthTokenError("Authentication token is invalid")

    user = await session.get(User, auth_token.user_id, with_for_update=True)

    if user is None or user.status == "disabled":
        raise InvalidAuthTokenError("Authentication token is invalid")

    auth_token.consumed_at = now
    user.status = "active"
    user.last_login_at = now
    await session.flush()
    return user


async def create_user_session(
    session: AsyncSession,
    *,
    user: User,
    expires_at: datetime,
    now: datetime,
) -> IssuedUserSession:
    """Create a revocable session; only its SHA-256 hash reaches PostgreSQL."""

    if user.status != "active":
        raise ValueError("Only active users may receive a session")

    if expires_at <= now:
        raise ValueError("expires_at must be in the future")

    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_secret(token),
            expires_at=expires_at,
        ),
    )
    await session.flush()

    return IssuedUserSession(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )


async def get_active_user_for_session(
    session: AsyncSession,
    *,
    token: str,
    now: datetime,
) -> User:
    """Resolve an opaque browser session without accepting a browser user ID."""

    user = await session.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == hash_secret(token),
            UserSession.expires_at > now,
            UserSession.revoked_at.is_(None),
            User.status == "active",
        ),
    )

    if user is None:
        raise InvalidUserSessionError("User session is invalid")

    return user


async def revoke_user_session(
    session: AsyncSession,
    *,
    token: str,
    now: datetime,
) -> None:
    """Revoke exactly one active session without deleting audit evidence."""

    user_session = await session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_secret(token)).with_for_update(),
    )

    if user_session is None or user_session.revoked_at is not None:
        raise InvalidUserSessionError("User session is invalid")

    user_session.revoked_at = now
    await session.flush()


async def inspect_invitation(
    session: AsyncSession,
    *,
    token: str,
    now: datetime,
) -> User:
    """Validate an invite without consuming it, so the registration form can be displayed."""

    auth_token = await session.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_secret(token))
    )
    if (
        auth_token is None
        or auth_token.purpose != "invite"
        or auth_token.consumed_at is not None
        or auth_token.expires_at <= now
    ):
        raise InvalidAuthTokenError("Authentication token is invalid")

    user = await session.get(User, auth_token.user_id)
    if user is None or user.status != "invited":
        raise InvalidAuthTokenError("Authentication token is invalid")

    return user


async def activate_invited_user_with_password(
    session: AsyncSession,
    *,
    token: str,
    password: str,
    now: datetime,
) -> User:
    """Consume one invite and set the password in one transaction."""

    auth_token = await session.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_secret(token)).with_for_update(),
    )
    if (
        auth_token is None
        or auth_token.purpose != "invite"
        or auth_token.consumed_at is not None
        or auth_token.expires_at <= now
    ):
        raise InvalidAuthTokenError("Authentication token is invalid")

    user = await session.get(User, auth_token.user_id, with_for_update=True)
    if user is None or user.status != "invited":
        raise InvalidAuthTokenError("Authentication token is invalid")

    # Token and account are locked together, so two concurrent registrations cannot both win.
    user.password_hash = PASSWORD_HASHER.hash(password)
    user.password_set_at = now
    user.status = "active"
    user.last_login_at = now
    auth_token.consumed_at = now
    await session.flush()
    return user


async def authenticate_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    now: datetime,
) -> User:
    """Authenticate without exposing whether an email address exists."""

    user = await session.scalar(
        select(User).where(User.email == normalize_email(email)).with_for_update()
    )

    if (
        user is None
        or user.status != "active"
        or user.password_hash is None
        or not PASSWORD_HASHER.verify(password, user.password_hash)
    ):
        raise InvalidCredentialsError("Invalid email or password")

    user.last_login_at = now
    await session.flush()
    return user
