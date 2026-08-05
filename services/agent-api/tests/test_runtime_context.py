from datetime import UTC, datetime

import pytest

from agent_api.agent import build_instructions
from agent_api.config import Settings
from agent_api.runtime_context import format_runtime_context_pack


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


def test_build_instructions_injects_runtime_context_before_overlay() -> None:
    fixed = datetime(2026, 8, 5, 3, 0, 0, tzinfo=UTC)
    text = build_instructions(
        overlay="VERTICAL_OVERLAY_MARKER",
        memory_block=None,
        mounted_names=set(),
        timezone_name="Asia/Shanghai",
        locale="zh-CN",
        now=fixed,
    )

    assert "Runtime context" in text
    assert "VERTICAL_OVERLAY_MARKER" in text
    assert text.index("Runtime context") < text.index("VERTICAL_OVERLAY_MARKER")
    assert text.index("AgentOS assistant") < text.index("Runtime context")


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
