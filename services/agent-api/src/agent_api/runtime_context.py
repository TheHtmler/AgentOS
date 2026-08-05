"""Per-run environment context injected into every Agent (platform foundation)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
