"""Expire pending HITL approvals and continue Runs with ToolDenied semantics."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from agent_api.db.chat_store import mark_interrupts_timed_out
from agent_api.db.models import Interrupt, Run, Thread
from agent_api.db.session import session_factory
from agent_api.hitl_resume import start_resume_background
from agent_api.runtime import AgentRuntime

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_INTERVAL_SECONDS = 30.0


async def sweep_expired_approvals(runtime: AgentRuntime) -> int:
    """Find waiting Runs with expired pending interrupts; auto-deny and resume.

    Returns the number of Runs for which a timeout resume was started.
    """

    now = datetime.now(UTC)
    async with session_factory() as session:
        run_ids = list(
            (
                await session.scalars(
                    select(Interrupt.run_id)
                    .join(Run, Run.id == Interrupt.run_id)
                    .where(
                        Interrupt.status == "pending",
                        Interrupt.expires_at <= now,
                        Run.status == "waiting_approval",
                    )
                    .distinct(),
                )
            ).all(),
        )

    started = 0
    for run_id in run_ids:
        try:
            async with session_factory() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None or run.status != "waiting_approval":
                    continue
                thread = await session.get(Thread, run.thread_id)
                if thread is None or thread.user_id is None:
                    continue
                user_id = thread.user_id
                key = f"timeout:{run_id}:{now.isoformat()}"
                resolved = await mark_interrupts_timed_out(
                    session,
                    run_id=run_id,
                    idempotency_key=key,
                )
            start_resume_background(
                runtime,
                run_id=run_id,
                user_id=user_id,
                interrupts=resolved,
            )
            started += 1
            logger.info("hitl timeout resumed run_id=%s", run_id)
        except Exception:
            logger.exception("hitl timeout sweep failed for run_id=%s", run_id)

    return started


async def hitl_timeout_loop(
    runtime: AgentRuntime,
    *,
    interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    stop_event: asyncio.Event,
) -> None:
    """Background loop that periodically sweeps expired approvals."""

    while not stop_event.is_set():
        try:
            await sweep_expired_approvals(runtime)
        except Exception:
            logger.exception("hitl timeout sweep crashed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
