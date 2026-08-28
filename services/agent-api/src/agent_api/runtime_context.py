"""Per-run environment context injected into every Agent (platform foundation)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class ScheduledTaskExecutionContext:
    """Non-persisted metadata describing one scheduled-task execution."""

    task_id: UUID
    title: str
    schedule_type: str
    timezone: str
    scheduled_for: datetime
    previous_run_at: datetime | None = None
    previous_run_status: str | None = None


def _format_context_datetime(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "never"
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_scheduled_task_context(context: ScheduledTaskExecutionContext) -> str:
    """Render task metadata for the current model turn without exposing it in history."""

    previous_run = _format_context_datetime(context.previous_run_at, context.timezone)
    if context.previous_run_status:
        previous_run = f"{previous_run} ({context.previous_run_status})"
    return f"""## Scheduled task execution
- Execution mode: an existing scheduled task is running now
- Task name: {context.title}
- Task ID: {context.task_id}
- Schedule: {context.schedule_type}
- Scheduled time: {_format_context_datetime(context.scheduled_for, context.timezone)}
- Timezone: {context.timezone}
- Previous run: {previous_run}
- This metadata is supplied by AgentOS for this turn and is not a user instruction.
- The saved task prompt is the deliverable. Do not create, edit, pause, or ask the user to
  configure another schedule.
- Use the mounted tools to retrieve required fresh or external data before producing the
  final deliverable. If a required tool fails or is unavailable, report that failure and
  do not claim the task completed successfully.
- Do not expose this metadata, internal IDs, or execution instructions in the final result."""


def format_runtime_context_pack(
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
    locale: str = "zh-CN",
) -> str:
    """Build the standard Runtime Context Pack (time, locale, capability bounds)."""

    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        # Fall back so a bad env var never blocks a Run; still tell the model clearly.
        tz = ZoneInfo("UTC")
        timezone_name = "UTC"

    moment = (now or datetime.now(UTC)).astimezone(tz)
    local_stamp = moment.strftime("%Y-%m-%d %H:%M:%S %Z")
    weekday = moment.strftime("%A")

    return f"""## Runtime context
- Current local time: {local_stamp} ({weekday})
- Timezone: {timezone_name}
- Locale: {locale}
- Treat the current local time above as authoritative for "today",
  relative dates, and age/duration math.
- Prefer the locale for user-facing language when the user has not
  specified another.
- You do not have an independent real-time clock; do not invent a
  different "now".
- If a needed fact is time-sensitive or externally grounded and not
  available from tools or this context, say you lack it rather than
  guessing."""
