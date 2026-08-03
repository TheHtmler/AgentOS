from datetime import UTC, datetime

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent_api.output_limits import (
    TRUNCATION_NOTICE,
    last_response_hit_length_limit,
    with_truncation_notice_if_needed,
)


def test_detects_length_finish_reason() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[TextPart(content="partial")],
            timestamp=datetime.now(UTC),
            finish_reason="length",
        ),
    ]
    assert last_response_hit_length_limit(messages) is True
    noted = with_truncation_notice_if_needed("partial code", messages)
    assert "partial code" in noted
    assert "截断" in noted
    assert TRUNCATION_NOTICE.strip() in noted


def test_ignores_normal_stop() -> None:
    messages = [
        ModelResponse(
            parts=[TextPart(content="done")],
            timestamp=datetime.now(UTC),
            finish_reason="stop",
        ),
    ]
    assert last_response_hit_length_limit(messages) is False
    assert with_truncation_notice_if_needed("done", messages) == "done"
