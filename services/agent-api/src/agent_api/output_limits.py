"""Helpers for detecting truncated model output (max_tokens / length stop)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse

TRUNCATION_NOTICE = (
    "\n\n---\n"
    "⚠️ 回复可能因长度限制被截断（`MODEL_MAX_OUTPUT_TOKENS`）。"
    "可直接回复「继续」让我接着写完，或在服务端调高该上限后重试。"
)


def last_response_hit_length_limit(messages: Sequence[ModelMessage]) -> bool:
    """True when the latest model response stopped because of the token budget."""

    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            return message.finish_reason == "length"
    return False


def with_truncation_notice_if_needed(
    content: str,
    messages: Sequence[ModelMessage],
) -> str:
    """Append a user-visible note when the model hit max output tokens."""

    if not last_response_hit_length_limit(messages):
        return content

    if TRUNCATION_NOTICE.strip() in content:
        return content

    return f"{content.rstrip()}{TRUNCATION_NOTICE}"
