"""Single-process outbox worker for loopback OpenClaw Weixin delivery."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import httpx
from sqlalchemy import or_, select

from agent_api.config import get_settings
from agent_api.db.models import ScheduledTaskNotification
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)
RETRY_DELAYS = (30, 120, 600, 1800, 7200)


class ScheduledNotificationWorker:
    def __init__(self) -> None:
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="scheduled-task-weixin-outbox")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if get_settings().scheduled_task_weixin_enabled:
                    await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduled notification worker iteration failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)

    async def process_one(self) -> None:
        now = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            row = await session.scalar(
                select(ScheduledTaskNotification)
                .where(
                    ScheduledTaskNotification.status.in_(("pending", "retrying", "unknown")),
                    ScheduledTaskNotification.next_attempt_at <= now,
                    or_(
                        ScheduledTaskNotification.lease_until.is_(None),
                        ScheduledTaskNotification.lease_until < now,
                    ),
                )
                .order_by(
                    ScheduledTaskNotification.next_attempt_at, ScheduledTaskNotification.created_at
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return
            row.status = "sending"
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=30)
            await session.flush()
            payload = {
                "delivery_id": str(row.delivery_id),
                "channel": row.channel,
                "account_id": row.account_id,
                "peer_id": row.peer_id,
                "text": row.text,
                "created_at": row.created_at.isoformat(),
            }
            delivery_id = row.id

        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.openclaw_delivery_shared_secret}"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.openclaw_delivery_timeout_seconds, connect=3),
                trust_env=False,
            ) as client:
                response = await client.post(
                    settings.openclaw_delivery_url, json=payload, headers=headers
                )
            data = cast(dict[str, Any], response.json() if response.content else {})
            if response.status_code >= 500 or response.status_code == 429:
                raise RuntimeError(f"transient_http_{response.status_code}")
            if response.status_code >= 400 or data.get("accepted") is not True:
                await self.finish(
                    delivery_id,
                    "skipped",
                    str(data.get("error_code") or f"http_{response.status_code}"),
                    None,
                )
                return
            await self.finish(delivery_id, "delivered", None, data.get("message_id"))
        except (httpx.HTTPError, RuntimeError) as error:
            await self.retry(delivery_id, str(error))

    async def finish(
        self, notification_id: UUID, status: str, error_code: str | None, message_id: str | None
    ) -> None:
        async with session_factory() as session, session.begin():
            row = await session.get(
                ScheduledTaskNotification, notification_id, with_for_update=True
            )
            if row is None:
                return
            row.status = status
            row.lease_until = None
            row.last_error_code = error_code
            row.openclaw_message_id = message_id
            row.delivered_at = datetime.now(UTC) if status == "delivered" else None

    async def retry(self, notification_id: UUID, error: str) -> None:
        async with session_factory() as session, session.begin():
            row = await session.get(
                ScheduledTaskNotification, notification_id, with_for_update=True
            )
            if row is None:
                return
            if row.attempts >= len(RETRY_DELAYS):
                row.status = "failed"
            else:
                row.status = "retrying"
                row.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=RETRY_DELAYS[row.attempts - 1]
                )
            row.lease_until = None
            row.last_error_code = "transient_delivery_error"
            row.last_error = error[:1000]
