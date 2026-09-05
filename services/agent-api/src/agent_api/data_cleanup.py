"""Bounded cleanup for expired operational rows and redundant stream deltas."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, exists, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import Settings
from agent_api.db.models import (
    AuthToken,
    ChannelBindingFlow,
    OpsSession,
    Run,
    RunEvent,
    UserSession,
)
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupReport:
    """Counts for rows that are safe to remove without altering user records."""

    auth_tokens: int = 0
    user_sessions: int = 0
    ops_sessions: int = 0
    binding_flows: int = 0
    text_deltas: int = 0


def _deleted_row_count(result: object) -> int:
    """SQLAlchemy exposes rowcount only on DML cursor results."""

    return cast(CursorResult[object], result).rowcount or 0


async def cleanup_operational_data(
    session: AsyncSession,
    *,
    now: datetime,
    text_delta_retention_days: int,
) -> CleanupReport:
    """Delete expired credentials and stream-only deltas from terminal Runs.

    Final messages and non-text Run events remain the durable audit record. The
    `Run` EXISTS predicate prevents cleanup from changing any active stream.
    """

    delta_cutoff = now - timedelta(days=text_delta_retention_days)

    auth_tokens = await session.execute(delete(AuthToken).where(AuthToken.expires_at <= now))
    user_sessions = await session.execute(delete(UserSession).where(UserSession.expires_at <= now))
    ops_sessions = await session.execute(delete(OpsSession).where(OpsSession.expires_at <= now))
    binding_flows = await session.execute(
        delete(ChannelBindingFlow).where(ChannelBindingFlow.expires_at <= now)
    )
    terminal_run = exists(
        select(Run.id).where(
            Run.id == RunEvent.run_id,
            Run.status.in_(("completed", "failed", "cancelled")),
        )
    )
    text_deltas = await session.execute(
        delete(RunEvent).where(
            RunEvent.event_type == "text_delta",
            RunEvent.created_at <= delta_cutoff,
            terminal_run,
        )
    )

    return CleanupReport(
        auth_tokens=_deleted_row_count(auth_tokens),
        user_sessions=_deleted_row_count(user_sessions),
        ops_sessions=_deleted_row_count(ops_sessions),
        binding_flows=_deleted_row_count(binding_flows),
        text_deltas=_deleted_row_count(text_deltas),
    )


async def data_cleanup_loop(*, settings: Settings, stop_event: asyncio.Event) -> None:
    """Run maintenance periodically without letting a failed pass stop the API."""

    while not stop_event.is_set():
        try:
            async with session_factory() as session, session.begin():
                report = await cleanup_operational_data(
                    session,
                    now=datetime.now(UTC),
                    text_delta_retention_days=settings.run_event_text_delta_retention_days,
                )
            if report != CleanupReport():
                logger.info("operational data cleanup completed: %s", report)
        except Exception:
            logger.exception("operational data cleanup failed")

        with suppress(TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.data_cleanup_interval_seconds
            )
