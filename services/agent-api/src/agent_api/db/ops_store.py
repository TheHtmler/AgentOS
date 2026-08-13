"""Ops-console session store and env-root password verification."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import Settings, get_settings
from agent_api.db.models import OpsSession

PASSWORD_HASHER = PasswordHash.recommended()


class OpsAuthNotConfiguredError(RuntimeError):
    """Raised when neither OPS_ROOT_PASSWORD nor OPS_ROOT_PASSWORD_HASH is set."""


class InvalidOpsCredentialsError(ValueError):
    """Raised when username/password do not match the env root."""


class InvalidOpsSessionError(ValueError):
    """Raised when an ops session token is missing, expired, or revoked."""


@dataclass(frozen=True, slots=True)
class IssuedOpsSession:
    subject: str
    token: str
    expires_at: datetime


def hash_secret(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()


def verify_ops_root_password(
    username: str,
    password: str,
    *,
    settings: Settings | None = None,
) -> bool:
    """Return True when credentials match the configured env root."""

    cfg = settings or get_settings()
    password_hash = cfg.ops_root_password_hash.strip()
    plain_password = cfg.ops_root_password
    if not password_hash and not plain_password:
        raise OpsAuthNotConfiguredError(
            "OPS_ROOT_PASSWORD or OPS_ROOT_PASSWORD_HASH is not configured",
        )
    if username.strip() != cfg.ops_root_username.strip():
        return False
    if password_hash:
        try:
            return PASSWORD_HASHER.verify(password, password_hash)
        except Exception:  # noqa: BLE001 — invalid hash format → treat as mismatch
            return False
    return secrets.compare_digest(password, plain_password)


async def create_ops_session(
    session: AsyncSession,
    *,
    subject: str,
    expires_at: datetime,
    now: datetime,
) -> IssuedOpsSession:
    if expires_at <= now:
        raise ValueError("expires_at must be in the future")

    token = secrets.token_urlsafe(32)
    session.add(
        OpsSession(
            token_hash=hash_secret(token),
            subject=subject,
            expires_at=expires_at,
        ),
    )
    await session.flush()
    return IssuedOpsSession(subject=subject, token=token, expires_at=expires_at)


async def get_ops_subject_by_token(
    session: AsyncSession,
    *,
    token: str,
    now: datetime,
) -> str:
    row = await session.scalar(
        select(OpsSession).where(
            OpsSession.token_hash == hash_secret(token),
            OpsSession.expires_at > now,
            OpsSession.revoked_at.is_(None),
        ),
    )
    if row is None:
        raise InvalidOpsSessionError("Ops session is invalid")
    return row.subject


async def revoke_ops_session(
    session: AsyncSession,
    *,
    token: str,
    now: datetime,
) -> None:
    row = await session.scalar(
        select(OpsSession).where(OpsSession.token_hash == hash_secret(token)).with_for_update(),
    )
    if row is None or row.revoked_at is not None:
        raise InvalidOpsSessionError("Ops session is invalid")
    row.revoked_at = now
    await session.flush()
