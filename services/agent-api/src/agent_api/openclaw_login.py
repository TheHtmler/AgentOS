"""Short-lived server-side coordination for OpenClaw Weixin QR logins."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx

from agent_api.config import get_settings

LOGIN_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class WeixinLoginSession:
    """A browser-safe handle to a loopback-only OpenClaw QR login."""

    id: UUID
    user_id: UUID
    adapter_session_id: str
    qrcode_url: str
    expires_at: datetime


class WeixinLoginCoordinator:
    """Keep QR-to-user correlation in process; credentials stay in OpenClaw."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, WeixinLoginSession] = {}
        self._lock = asyncio.Lock()

    async def start(self, user_id: UUID) -> WeixinLoginSession:
        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.openclaw_delivery_shared_secret}"}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=3),
            trust_env=False,
        ) as client:
            response = await client.post(settings.openclaw_provisioning_url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"OpenClaw QR login start failed: HTTP {response.status_code}")
        raw_payload: object = response.json()
        if not isinstance(raw_payload, Mapping):
            raise RuntimeError("OpenClaw QR login returned an invalid response")
        payload = cast(Mapping[str, object], raw_payload)
        adapter_session_id = payload.get("session_id")
        qrcode_url = payload.get("qrcode_url")
        if not isinstance(adapter_session_id, str) or not isinstance(qrcode_url, str):
            raise RuntimeError("OpenClaw QR login did not return a QR code")

        session = WeixinLoginSession(
            id=uuid4(),
            user_id=user_id,
            adapter_session_id=adapter_session_id,
            qrcode_url=qrcode_url,
            expires_at=datetime.now(UTC) + LOGIN_TTL,
        )
        async with self._lock:
            self._sessions = {
                key: value
                for key, value in self._sessions.items()
                if value.expires_at > datetime.now(UTC)
            }
            self._sessions[session.id] = session
        return session

    async def get(self, login_id: UUID, user_id: UUID) -> WeixinLoginSession | None:
        async with self._lock:
            session = self._sessions.get(login_id)
            if (
                session is None
                or session.user_id != user_id
                or session.expires_at <= datetime.now(UTC)
            ):
                return None
            return session

    async def status(self, session: WeixinLoginSession) -> Mapping[str, object]:
        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.openclaw_delivery_shared_secret}"}
        url = f"{settings.openclaw_provisioning_url}/{session.adapter_session_id}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=3),
            trust_env=False,
        ) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"OpenClaw QR login status failed: HTTP {response.status_code}")
        raw_payload: object = response.json()
        if not isinstance(raw_payload, Mapping):
            raise RuntimeError("OpenClaw QR login status returned an invalid response")
        payload = cast(Mapping[str, object], raw_payload)
        if not isinstance(payload.get("status"), str):
            raise RuntimeError("OpenClaw QR login status returned an invalid response")
        return payload


weixin_login_coordinator = WeixinLoginCoordinator()
