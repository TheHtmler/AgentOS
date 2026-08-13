"""Ops console authentication (env root + ops_sessions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from agent_api.config import get_settings
from agent_api.db.ops_store import (
    InvalidOpsSessionError,
    OpsAuthNotConfiguredError,
    create_ops_session,
    get_ops_subject_by_token,
    revoke_ops_session,
    verify_ops_root_password,
)
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops", tags=["ops"])

OPS_SESSION_COOKIE = "ops_session"


class OpsLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class OpsLoginResponse(BaseModel):
    subject: str
    session_token: str
    expires_at: datetime


class OpsMeResponse(BaseModel):
    subject: str


async def get_ops_subject(
    ops_session: Annotated[str | None, Cookie(alias=OPS_SESSION_COOKIE)] = None,
) -> str:
    if not ops_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ops authentication required",
        )
    now = datetime.now(UTC)
    try:
        async with session_factory() as session:
            return await get_ops_subject_by_token(session, token=ops_session, now=now)
    except InvalidOpsSessionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ops authentication required",
        ) from error


@router.post("/login", response_model=OpsLoginResponse)
async def ops_login(payload: OpsLoginRequest, response: Response) -> OpsLoginResponse:
    settings = get_settings()
    try:
        ok = verify_ops_root_password(
            payload.username,
            payload.password,
            settings=settings,
        )
    except OpsAuthNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ops root password is not configured (set OPS_ROOT_PASSWORD)",
        ) from error

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ops credentials",
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.ops_session_ttl_hours)
    async with session_factory() as session, session.begin():
        issued = await create_ops_session(
            session,
            subject=settings.ops_root_username.strip(),
            expires_at=expires_at,
            now=now,
        )

    response.set_cookie(
        key=OPS_SESSION_COOKIE,
        value=issued.token,
        httponly=True,
        samesite="lax",
        max_age=int(settings.ops_session_ttl_hours * 3600),
        path="/",
    )
    return OpsLoginResponse(
        subject=issued.subject,
        session_token=issued.token,
        expires_at=issued.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def ops_logout(
    response: Response,
    ops_session: Annotated[str | None, Cookie(alias=OPS_SESSION_COOKIE)] = None,
) -> None:
    if ops_session:
        now = datetime.now(UTC)
        try:
            async with session_factory() as session, session.begin():
                await revoke_ops_session(session, token=ops_session, now=now)
        except InvalidOpsSessionError:
            pass
    response.delete_cookie(OPS_SESSION_COOKIE, path="/")


@router.get("/me", response_model=OpsMeResponse)
async def ops_me(subject: Annotated[str, Depends(get_ops_subject)]) -> OpsMeResponse:
    return OpsMeResponse(subject=subject)
