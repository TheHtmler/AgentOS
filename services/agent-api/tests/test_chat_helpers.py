"""Pure tests for durable model-history sanitization and observability helpers."""

from typing import cast
from uuid import uuid4

import pytest
from pydantic_ai import ModelMessagesTypeAdapter

from agent_api.api.chat import (
    persist_context_budget_event,
    schedule_context_budget_event,
    strip_thinking_parts,
)
from agent_api.context_budget import BudgetReport


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


@pytest.mark.anyio
async def test_persist_context_budget_event_noop_without_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_session_factory() -> object:
        raise AssertionError("no trim actions -> no DB write")

    monkeypatch.setattr("agent_api.api.chat.session_factory", forbidden_session_factory)
    report = BudgetReport(history_before_tokens=10, history_after_tokens=10, budget_tokens=100)
    await persist_context_budget_event(uuid4(), report, phase="pre_run")


def test_schedule_context_budget_event_noop_without_actions() -> None:
    report = BudgetReport(history_before_tokens=10, history_after_tokens=10, budget_tokens=100)
    schedule_context_budget_event(uuid4(), report, phase="step")


def test_schedule_context_budget_event_without_loop_is_noop() -> None:
    report = BudgetReport(
        history_before_tokens=9000,
        history_after_tokens=5000,
        budget_tokens=8000,
        dropped_runs=1,
        actions=["dropped 1 oldest run(s)"],
    )
    schedule_context_budget_event(uuid4(), report, phase="step")
