"""Transactional enqueue and bounded state helpers for scheduled notifications."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.models import (
    Run,
    ScheduledTask,
    ScheduledTaskNotification,
    User,
    UserChannelBinding,
)

MAX_WEIXIN_TEXT = 4_000


async def enqueue_for_completed_run(
    session: AsyncSession,
    *,
    run: Run,
    assistant_content: str,
) -> ScheduledTaskNotification | None:
    """Create exactly one outbox row in the same transaction as Run completion."""

    if run.scheduled_task_id is None:
        return None
    task = await session.scalar(
        select(ScheduledTask).where(ScheduledTask.id == run.scheduled_task_id)
    )
    if (
        task is None
        or not task.notification_enabled
        or task.notification_channel != "openclaw-weixin"
    ):
        return None
    binding = None
    if task.notification_binding_id is not None:
        binding = await session.scalar(
            select(UserChannelBinding).where(UserChannelBinding.id == task.notification_binding_id)
        )
    user = await session.get(User, task.owner_user_id)
    if binding is None or user is None:
        return None
    text = assistant_content.strip()[:MAX_WEIXIN_TEXT]
    if not text:
        return None
    existing = await session.scalar(
        select(ScheduledTaskNotification).where(
            ScheduledTaskNotification.run_id == run.id,
            ScheduledTaskNotification.channel == "openclaw-weixin",
        )
    )
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    notification = ScheduledTaskNotification(
        task_id=task.id,
        run_id=run.id,
        user_id=user.id,
        binding_id=binding.id,
        channel="openclaw-weixin",
        account_id=binding.account_id,
        peer_id=binding.peer_id,
        text=text,
        status="pending" if get_settings().scheduled_task_weixin_enabled else "skipped",
        next_attempt_at=now,
        last_error_code=None
        if get_settings().scheduled_task_weixin_enabled
        else "feature_disabled",
    )
    session.add(notification)
    task.notification_last_status = notification.status
    task.notification_last_error_code = notification.last_error_code
    task.notification_last_at = now
    await session.flush()
    return notification
