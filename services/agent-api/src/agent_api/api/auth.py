from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from agent_api.config import get_settings
from agent_api.db.auth_store import (
    InvalidAuthTokenError,
    InvalidCredentialsError,
    InvalidUserSessionError,
    activate_invited_user_with_password,
    authenticate_user,
    consume_auth_token,
    create_invited_user,
    create_user_session,
    find_user_by_email,
    get_active_user_for_session,
    inspect_invitation,
    issue_auth_token,
    normalize_email,
    revoke_user_session,
    validate_new_password,
)
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/auth", tags=["auth"])

SESSION_COOKIE_NAME = "agentos_session"


class VerifyAuthTokenRequest(BaseModel):
    """One token copied from an invitation or magic-link URL."""

    token: Annotated[str, Field(min_length=1, max_length=512)]
    purpose: Literal["invite", "magic_link"]


class AuthSessionResponse(BaseModel):
    """Returned only to the trusted Next.js Route Handler."""

    user_id: UUID
    email: str
    session_token: str
    expires_at: datetime


class CurrentUserResponse(BaseModel):
    """The minimal identity that is safe to expose to the signed-in browser."""

    id: UUID
    email: str
    can_manage_invitations: bool


class InvitationResponse(BaseModel):
    """A one-time URL returned only to a configured invite manager."""

    email: str
    invitation_url: str
    expires_at: datetime


class InspectInvitationRequest(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=512)]


class RegisterRequest(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=512)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class EmailAddressRequest(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=320)]

    @field_validator("email")
    @classmethod
    def normalize_and_validate_email(cls, value: str) -> str:
        # Request validation returns HTTP 422 before any database query runs.
        return normalize_email(value)


class CreateInvitationRequest(EmailAddressRequest):
    """The email address that will receive a manually distributed invite URL."""


class LoginRequest(EmailAddressRequest):
    password: Annotated[str, Field(min_length=1, max_length=128)]

def session_token_from_request(request: Request) -> str:
    """Read the opaque cookie without ever accepting a browser-provided user ID."""

    token = request.cookies.get(SESSION_COOKIE_NAME)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return token


async def get_current_user(request: Request) -> User:
    """FastAPI dependency for future owner-scoped API endpoints."""

    token = session_token_from_request(request)

    try:
        async with session_factory() as session:
            return await get_active_user_for_session(
                session,
                token=token,
                now=datetime.now(UTC),
            )
    except InvalidUserSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        ) from error


def require_invite_manager(user: User) -> None:
    """Authorize invitation creation from server configuration, never browser input."""

    if user.email not in get_settings().admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invitation management is not permitted",
        )


@router.post("/verify", response_model=AuthSessionResponse)
async def verify_auth_token(payload: VerifyAuthTokenRequest) -> AuthSessionResponse:
    """Consume a one-time token and create a revocable 30-day browser session."""

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=get_settings().auth_session_ttl_days)

    try:
        async with session_factory() as session, session.begin():
            user = await consume_auth_token(
                session,
                token=payload.token,
                purpose=payload.purpose,
                now=now,
            )
            issued_session = await create_user_session(
                session,
                user=user,
                expires_at=expires_at,
                now=now,
            )
    except InvalidAuthTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token is invalid or expired",
        ) from error

    return AuthSessionResponse(
        user_id=user.id,
        email=user.email,
        session_token=issued_session.token,
        expires_at=issued_session.expires_at,
    )


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_auth_user(
    user: Annotated[User, Depends(get_current_user)],
) -> CurrentUserResponse:
    """Return the authenticated user without exposing session tokens."""

    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        can_manage_invitations=user.email in get_settings().admin_emails,
    )


@router.post("/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: CreateInvitationRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> InvitationResponse:
    """Create or replace one pending invite; delivery is deliberately outside this API."""

    require_invite_manager(user)
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=get_settings().auth_invite_ttl_minutes)

    try:
        async with session_factory() as session, session.begin():
            invited_user = await find_user_by_email(session, email=payload.email)
            if invited_user is None:
                invited_user = await create_invited_user(session, email=payload.email)
            elif invited_user.status != "invited":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An active or disabled user already uses this email",
                )

            issued_invitation = await issue_auth_token(
                session,
                user=invited_user,
                purpose="invite",
                expires_at=expires_at,
                now=now,
            )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error

    query = urlencode({"token": issued_invitation.token})
    return InvitationResponse(
        email=invited_user.email,
        invitation_url=f"{get_settings().web_app_origin}/register?{query}",
        expires_at=issued_invitation.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    """Revoke the current opaque session while retaining its audit record."""

    token = session_token_from_request(request)

    try:
        async with session_factory() as session, session.begin():
            await revoke_user_session(
                session,
                token=token,
                now=datetime.now(UTC),
            )
    except InvalidUserSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/inspect")
async def inspect_invitation_endpoint(payload: InspectInvitationRequest) -> dict[str, str]:
    """Return the email bound to an invite, but never consume the invite here."""

    try:
        async with session_factory() as session:
            user = await inspect_invitation(
                session,
                token=payload.token,
                now=datetime.now(UTC),
            )
    except InvalidAuthTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invitation is invalid or expired",
        ) from error

    return {"email": user.email}


@router.post("/register", response_model=AuthSessionResponse)
async def register(payload: RegisterRequest) -> AuthSessionResponse:
    """Set the first password, activate the invited account, then sign in."""

    try:
        validate_new_password(payload.password)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=get_settings().auth_session_ttl_days)

        async with session_factory() as session, session.begin():
            user = await activate_invited_user_with_password(
                session,
                token=payload.token,
                password=payload.password,
                now=now,
            )
            issued_session = await create_user_session(
                session,
                user=user,
                expires_at=expires_at,
                now=now,
            )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    return AuthSessionResponse(
        user_id=user.id,
        email=user.email,
        session_token=issued_session.token,
        expires_at=issued_session.expires_at,
    )


@router.post("/login", response_model=AuthSessionResponse)
async def login(payload: LoginRequest) -> AuthSessionResponse:
    """Create a new revocable browser session after password verification."""

    try:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=get_settings().auth_session_ttl_days)

        async with session_factory() as session, session.begin():
            user = await authenticate_user(
                session,
                email=payload.email,
                password=payload.password,
                now=now,
            )
            issued_session = await create_user_session(
                session,
                user=user,
                expires_at=expires_at,
                now=now,
            )
    except InvalidCredentialsError as error:
        # 固定文案和状态码，避免用接口枚举已注册邮箱。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from error

    return AuthSessionResponse(
        user_id=user.id,
        email=user.email,
        session_token=issued_session.token,
        expires_at=issued_session.expires_at,
    )
