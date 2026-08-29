from datetime import UTC, datetime

import pytest

from agent_api.scheduled_tasks import (
    ScheduleValidationError,
    next_run_at,
    normalize_schedule_config,
)


def test_daily_schedule_uses_the_next_local_occurrence() -> None:
    now = datetime(2026, 8, 29, 1, 30, tzinfo=UTC)
    config = normalize_schedule_config("daily", time_of_day="10:00", timezone_name="Asia/Shanghai")

    result = next_run_at("daily", config, "Asia/Shanghai", now=now)

    assert result == datetime(2026, 8, 29, 2, 0, tzinfo=UTC)


def test_weekly_schedule_normalizes_days_and_rolls_forward() -> None:
    config = normalize_schedule_config(
        "weekly",
        time_of_day="09:05",
        days_of_week=[6, 0, 6],
        timezone_name="UTC",
    )

    assert config == {"time_of_day": "09:05", "days_of_week": [0, 6]}
    assert next_run_at(
        "weekly",
        config,
        "UTC",
        now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
    ) == datetime(2026, 8, 30, 9, 5, tzinfo=UTC)


def test_monthly_day_31_skips_short_months() -> None:
    config = normalize_schedule_config(
        "monthly",
        time_of_day="08:00",
        day_of_month=31,
        timezone_name="UTC",
    )

    assert next_run_at(
        "monthly",
        config,
        "UTC",
        now=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
    ) == datetime(2026, 3, 31, 8, 0, tzinfo=UTC)


def test_monthly_short_month_can_run_on_its_last_day() -> None:
    config = normalize_schedule_config(
        "monthly",
        time_of_day="08:00",
        day_of_month=31,
        month_end_policy="last_day",
        timezone_name="UTC",
    )

    assert next_run_at(
        "monthly",
        config,
        "UTC",
        now=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
    ) == datetime(2026, 2, 28, 8, 0, tzinfo=UTC)


def test_monthly_last_day_is_explicit() -> None:
    config = normalize_schedule_config(
        "monthly",
        time_of_day="08:00",
        monthly_mode="last_day",
        timezone_name="UTC",
    )

    assert config == {"time_of_day": "08:00", "monthly_mode": "last_day"}
    assert next_run_at(
        "monthly",
        config,
        "UTC",
        now=datetime(2026, 2, 27, 9, 0, tzinfo=UTC),
    ) == datetime(2026, 2, 28, 8, 0, tzinfo=UTC)


def test_one_time_schedule_converts_an_offset_to_the_named_timezone() -> None:
    config = normalize_schedule_config(
        "once",
        run_at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        timezone_name="Asia/Shanghai",
    )

    assert config == {"run_at": "2026-08-29T18:00"}


@pytest.mark.parametrize(
    "schedule_type, time_of_day, days_of_week, day_of_month",
    [
        ("once", None, None, None),
        ("daily", "25:00", None, None),
        ("weekly", "10:00", [], None),
        ("monthly", "10:00", None, 32),
    ],
)
def test_invalid_schedule_is_rejected(
    schedule_type: str,
    time_of_day: str | None,
    days_of_week: list[int] | None,
    day_of_month: int | None,
) -> None:
    with pytest.raises(ScheduleValidationError):
        normalize_schedule_config(
            schedule_type,
            time_of_day=time_of_day,
            days_of_week=days_of_week,
            day_of_month=day_of_month,
            timezone_name="UTC",
        )
