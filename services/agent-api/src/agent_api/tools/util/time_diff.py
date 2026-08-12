"""Deterministic time difference helper with injectable clock for tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ALL_UNITS = ("days", "hours", "minutes", "months", "years")


def compute_time_diff(
    *,
    start: str,
    end: str | None = None,
    timezone: str | None = None,
    units: list[str] | None = None,
    now: datetime | None = None,
    default_timezone: str = "Asia/Shanghai",
) -> dict[str, object]:
    """Compute signed deltas between two instants.

    Returns ``{ok, start, end, timezone, delta}`` or
    ``{ok: False, error_code, message}``.
    """

    if not start or not str(start).strip():
        return {
            "ok": False,
            "error_code": "empty_start",
            "message": "start is required",
        }

    zone_name = (timezone or default_timezone).strip()
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return {
            "ok": False,
            "error_code": "bad_timezone",
            "message": f"invalid IANA timezone: {zone_name}",
        }

    selected = list(ALL_UNITS) if units is None else list(units)
    if not selected or any(unit not in ALL_UNITS for unit in selected):
        return {
            "ok": False,
            "error_code": "bad_units",
            "message": f"units must be a non-empty subset of {list(ALL_UNITS)}",
        }

    start_dt = _parse_instant(start, zone=zone, error_code="bad_start")
    if isinstance(start_dt, dict):
        return start_dt

    if end is None or not str(end).strip():
        moment = now if now is not None else datetime.now(UTC)
        if moment.tzinfo is None:
            end_dt = moment.replace(tzinfo=UTC).astimezone(zone)
        else:
            end_dt = moment.astimezone(zone)
    else:
        parsed_end = _parse_instant(end, zone=zone, error_code="bad_end")
        if isinstance(parsed_end, dict):
            return parsed_end
        end_dt = parsed_end

    delta: dict[str, Any] = {}
    seconds = (end_dt - start_dt).total_seconds()
    if "days" in selected:
        delta["days"] = seconds / 86_400.0
    if "hours" in selected:
        delta["hours"] = seconds / 3_600.0
    if "minutes" in selected:
        delta["minutes"] = seconds / 60.0
    if "months" in selected:
        delta["months"] = _calendar_months(start_dt, end_dt)
    if "years" in selected:
        delta["years"] = _calendar_years(start_dt, end_dt)

    return {
        "ok": True,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "timezone": zone_name,
        "delta": delta,
    }


def _parse_instant(
    raw: str,
    *,
    zone: ZoneInfo,
    error_code: str,
) -> datetime | dict[str, object]:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    # Date-only → local midnight in the resolved timezone.
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return {
                "ok": False,
                "error_code": error_code,
                "message": f"invalid date: {raw}",
            }
        return datetime.combine(day, time.min, tzinfo=zone)

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return {
            "ok": False,
            "error_code": error_code,
            "message": f"invalid datetime: {raw}",
        }

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _calendar_months(start: datetime, end: datetime) -> int:
    """Whole calendar months between instants (signed)."""

    if end < start:
        return -_calendar_months(end, start)
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def _calendar_years(start: datetime, end: datetime) -> int:
    """Whole calendar years between instants (signed)."""

    if end < start:
        return -_calendar_years(end, start)
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years
