from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_api.agent import build_context_snapshot, build_instructions
from agent_api.config import Settings
from agent_api.runtime_context import (
    ScheduledTaskExecutionContext,
    format_runtime_context_pack,
    format_scheduled_task_context,
)


def test_runtime_context_pack_includes_time_locale_and_bounds() -> None:
    fixed = datetime(2026, 8, 5, 11, 0, 0, tzinfo=UTC)
    text = format_runtime_context_pack(
        now=fixed,
        timezone_name="Asia/Shanghai",
        locale="zh-CN",
    )

    assert "## Runtime context" in text
    assert "Asia/Shanghai" in text
    assert "zh-CN" in text
    assert "2026-08-05" in text
    assert "independent real-time clock" in text
    assert "guessing" in text


def test_context_snapshot_carries_runtime_pack_before_data_blocks() -> None:
    fixed = datetime(2026, 8, 5, 3, 0, 0, tzinfo=UTC)
    text = build_context_snapshot(
        memory_block="## Known user facts\n- x",
        timezone_name="Asia/Shanghai",
        locale="zh-CN",
        now=fixed,
    )

    assert text is not None
    assert "Runtime context" in text
    assert "2026-08-05" in text
    assert text.index("Runtime context") < text.index("Known user facts")

    # Stable instructions no longer carry the per-second runtime pack.
    instructions = build_instructions(overlay=None, mounted_names=set())
    assert "Runtime context" not in instructions


def test_scheduled_context_is_explicit_and_turn_scoped() -> None:
    task_id = uuid4()
    context = ScheduledTaskExecutionContext(
        task_id=task_id,
        title="Github热榜",
        schedule_type="daily",
        timezone="Asia/Shanghai",
        scheduled_for=datetime(2026, 8, 29, 1, 0, tzinfo=UTC),
        previous_run_at=None,
    )

    block = format_scheduled_task_context(context)
    snapshot = build_context_snapshot(
        scheduled_task_context=context,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
    )

    assert "## Scheduled task execution" in block
    assert "Github热榜" in block
    assert str(task_id) in block
    assert "Do not create, edit, pause" in block
    assert snapshot is not None
    assert "Scheduled task execution" in snapshot
    assert "2026-08-29 09:00:00" in snapshot


def test_settings_accept_runtime_timezone_and_locale() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            "runtime_timezone": "Asia/Tokyo",
            "runtime_locale": "en-US",
        },
    )
    assert settings.runtime_timezone == "Asia/Tokyo"
    assert settings.runtime_locale == "en-US"


def test_settings_reject_invalid_timezone() -> None:
    with pytest.raises(ValueError, match="runtime_timezone"):
        Settings.model_validate(
            {
                "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
                "runtime_timezone": "Not/AZone",
            },
        )
