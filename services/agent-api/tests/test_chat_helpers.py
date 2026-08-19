"""Pure tests for durable model-history sanitization."""

from typing import cast

from pydantic_ai import ModelMessagesTypeAdapter

from agent_api.api.chat import strip_thinking_parts


def test_strip_thinking_parts_removes_readable_reasoning() -> None:
    messages = cast(
        list[dict[str, object]],
        [
            {
                "kind": "response",
                "parts": [
                    {"part_kind": "thinking", "content": "private summary"},
                    {"part_kind": "text", "content": "answer"},
                ],
            }
        ],
    )

    sanitized = strip_thinking_parts(messages)

    assert sanitized == [
        {
            "kind": "response",
            "parts": [{"part_kind": "text", "content": "answer"}],
        }
    ]
    assert len(ModelMessagesTypeAdapter.validate_python(sanitized)) == 1


def test_strip_thinking_parts_keeps_only_responses_continuation_metadata() -> None:
    messages = cast(
        list[dict[str, object]],
        [
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "thinking",
                        "content": "readable summary",
                        "id": "rs_123",
                        "signature": "opaque-signature",
                        "provider_name": "openai",
                        "provider_details": {"raw_content": "private reasoning"},
                    }
                ],
            }
        ],
    )

    assert strip_thinking_parts(messages) == [
        {
            "kind": "response",
            "parts": [
                {
                    "part_kind": "thinking",
                    "content": "",
                    "id": "rs_123",
                    "signature": "opaque-signature",
                    "provider_name": "openai",
                }
            ],
        }
    ]


def test_strip_thinking_parts_drops_incomplete_continuation_metadata() -> None:
    messages = cast(
        list[dict[str, object]],
        [
            {
                "kind": "response",
                "parts": [
                    {"part_kind": "thinking", "id": "rs_123"},
                    {"part_kind": "text", "content": "answer"},
                ],
            }
        ],
    )

    assert strip_thinking_parts(messages)[0]["parts"] == [
        {"part_kind": "text", "content": "answer"}
    ]
