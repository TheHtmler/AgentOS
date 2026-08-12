from datetime import datetime
from zoneinfo import ZoneInfo

from agent_api.tools.util.time_diff import compute_time_diff


def test_same_day_hours() -> None:
    out = compute_time_diff(
        start="2026-08-12T08:00:00",
        end="2026-08-12T11:30:00",
        timezone="Asia/Shanghai",
        units=["hours", "minutes"],
    )
    assert out["ok"] is True
    assert out["delta"]["hours"] == 3.5
    assert out["delta"]["minutes"] == 210.0
    assert "days" not in out["delta"]


def test_date_only_days() -> None:
    out = compute_time_diff(
        start="2026-08-01",
        end="2026-08-12",
        timezone="Asia/Shanghai",
        units=["days"],
    )
    assert out["ok"] is True
    assert out["delta"]["days"] == 11.0


def test_default_end_uses_injected_now() -> None:
    # Inject local midnight so absolute day delta is exact (not UTC/CST skew).
    out = compute_time_diff(
        start="2026-08-10",
        timezone="Asia/Shanghai",
        units=["days"],
        now=datetime(2026, 8, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert out["ok"] is True
    assert out["delta"]["days"] == 2.0


def test_calendar_months_and_years() -> None:
    out = compute_time_diff(
        start="2024-01-31",
        end="2024-03-01",
        timezone="Asia/Shanghai",
        units=["months", "years"],
    )
    assert out["ok"] is True
    assert out["delta"]["months"] == 1
    assert out["delta"]["years"] == 0


def test_bad_timezone() -> None:
    out = compute_time_diff(start="2026-01-01", end="2026-01-02", timezone="Not/AZone")
    assert out["ok"] is False
    assert out["error_code"] == "bad_timezone"
