from __future__ import annotations

import re

from agent_api.knowledge.types import ChunkSpec

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？])|\n")
_BLANK_LINE_SPLIT = re.compile(r"\n\s*\n")
_TITLE_MAX_LEN = 80


def chunk_text(text: str, *, max_chars: int = 1200) -> list[ChunkSpec]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be empty")

    segments: list[str] = []
    for paragraph in _BLANK_LINE_SPLIT.split(stripped):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            segments.append(paragraph)
        else:
            segments.extend(_split_long_segment(paragraph, max_chars))

    if not segments:
        raise ValueError("text must not be empty")

    return [
        ChunkSpec(
            chunk_index=index,
            title=_chunk_title(content, index),
            content=content,
        )
        for index, content in enumerate(segments)
    ]


def _split_long_segment(text: str, max_chars: int) -> list[str]:
    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part]
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not current:
            candidate = sentence
        else:
            candidate = current + sentence

        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(sentence) <= max_chars:
            current = sentence
        else:
            chunks.extend(_hard_slice(sentence, max_chars))

    if current:
        chunks.append(current)

    return chunks


def _hard_slice(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def _chunk_title(content: str, index: int) -> str:
    first_line = content.strip().splitlines()[0].strip()
    if first_line:
        if len(first_line) <= _TITLE_MAX_LEN:
            return first_line
        return first_line[:_TITLE_MAX_LEN]
    return f"第 {index + 1} 段"
