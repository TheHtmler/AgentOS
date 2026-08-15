"""Helpers to attach upload originals onto AG-UI / pydantic-ai user prompts."""

from __future__ import annotations

import base64
from typing import Any

from ag_ui.core import ImageInputContent, InputContentDataSource, TextInputContent, UserMessage
from pydantic_ai.messages import BinaryContent


def user_prompt_with_vision(text: str, vision_parts: list[BinaryContent]) -> str | list[Any]:
    """Return text-only or multimodal user prompt for ``agent.run``."""

    if not vision_parts:
        return text
    return [text, *vision_parts]


def enrich_ag_ui_user_message(
    user_message: UserMessage,
    *,
    text: str,
    vision_parts: list[BinaryContent],
) -> UserMessage:
    """Rebuild the AG-UI user message with image parts for vision models."""

    if not vision_parts:
        return user_message

    content: list[TextInputContent | ImageInputContent] = [
        TextInputContent(text=text),
    ]
    for index, part in enumerate(vision_parts):
        content.append(
            ImageInputContent(
                source=InputContentDataSource(
                    type="data",
                    value=base64.b64encode(part.data).decode("ascii"),
                    mime_type=str(part.media_type),
                ),
                metadata={"filename": f"upload-{index}"},
            )
        )
    return user_message.model_copy(update={"content": content})
