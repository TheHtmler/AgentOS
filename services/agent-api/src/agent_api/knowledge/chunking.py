"""Structure-aware chunking for knowledge ingest.

Follows the common RAG pipeline used by Dify / LlamaIndex / LangChain:
detect headings → merge hard-wrapped lines → pack paragraphs → overlap.
Each chunk keeps a section label and repeats the heading in the body so
retrieval still sees the topic when a hit is only the middle of a section.
"""

from __future__ import annotations

import re

from agent_api.knowledge.types import ChunkSpec

DEFAULT_MAX_CHARS = 900
DEFAULT_OVERLAP = 150
_TITLE_MAX_LEN = 48

_PAGE_MARK = re.compile(r"^\[\s*第\s*(\d+)\s*页\s*\]$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(\S.*)$")
_CHAPTER_HEADING = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节部分篇]\s*\S*")
_ENUM_HEADING = re.compile(r"^[（(]?[一二三四五六七八九十]+[)）、.．]\s*\S+")
_NUMBERED_HEADING = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}\.\s+\S+")
_SENTENCE_END = re.compile(r"[。！？!?．.…][”’」』]?\s*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])")
_CJK_START = re.compile(r"^[\u4e00-\u9fff]")
_LIST_ITEM = re.compile(r"^(?:[-*•·]|\d+[)）]、.])\s+")


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[ChunkSpec]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be empty")

    packed = _pack_sections(stripped, max_chars=max_chars, overlap=overlap)
    if not packed:
        raise ValueError("text must not be empty")

    return [
        ChunkSpec(
            chunk_index=index,
            title=_chunk_title(body, heading),
            content=_contextualize(body, heading),
            section_label=_section_label(heading, page),
        )
        for index, (heading, page, body) in enumerate(packed)
    ]


def _pack_sections(
    text: str,
    *,
    max_chars: int,
    overlap: int,
) -> list[tuple[str | None, str | None, str]]:
    chunks: list[tuple[str | None, str | None, str]] = []
    heading: str | None = None
    page: str | None = None
    paragraphs: list[str] = []

    def flush() -> None:
        nonlocal paragraphs
        for body in _pack_paragraphs(paragraphs, max_chars=max_chars, overlap=overlap):
            chunks.append((heading, page, body))
        paragraphs = []

    for kind, value in _iter_blocks(text):
        if kind == "page":
            flush()
            page = value
            continue
        if kind == "heading":
            flush()
            heading = value
            continue
        paragraphs.append(value)

    flush()
    return chunks


def _iter_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        blocks.append(("para", _join_wrapped(buffer)))
        buffer.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush_buffer()
            continue
        page = _PAGE_MARK.match(line)
        if page is not None:
            flush_buffer()
            blocks.append(("page", page.group(1)))
            continue
        heading = _heading_text(line)
        if heading is not None:
            flush_buffer()
            blocks.append(("heading", heading))
            continue
        if buffer and not _should_join(buffer[-1], line):
            flush_buffer()
        buffer.append(line)

    flush_buffer()
    return blocks


def _heading_text(line: str) -> str | None:
    markdown = _MARKDOWN_HEADING.match(line)
    if markdown is not None:
        return markdown.group(1).strip()
    if _PAGE_MARK.match(line):
        return None
    if len(line) > 40:
        return None
    if _CHAPTER_HEADING.match(line) or _ENUM_HEADING.match(line) or _NUMBERED_HEADING.match(line):
        return line
    return None


def _should_join(previous: str, current: str) -> bool:
    if _LIST_ITEM.match(current):
        return False
    return not _SENTENCE_END.search(previous)


def _join_wrapped(lines: list[str]) -> str:
    joined = lines[0]
    for line in lines[1:]:
        if _CJK_START.search(line):
            joined += line
        else:
            joined += f" {line}"
    return joined


def _pack_paragraphs(paragraphs: list[str], *, max_chars: int, overlap: int) -> list[str]:
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces = [paragraph]
        else:
            pieces = _split_long_segment(paragraph, max_chars)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
                carry = _overlap_prefix(current, overlap)
                current = f"{carry}\n\n{piece}" if carry else piece
                if len(current) > max_chars:
                    chunks.extend(_split_long_segment(current, max_chars))
                    current = ""
            else:
                chunks.extend(_split_long_segment(piece, max_chars))

    if current:
        chunks.append(current)
    return chunks


def _split_long_segment(text: str, max_chars: int) -> list[str]:
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not sentences:
        return _hard_slice(text, max_chars)

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current}{sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(sentence) <= max_chars:
            current = sentence
        else:
            chunks.extend(_hard_slice(sentence, max_chars))
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _hard_slice(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def _overlap_prefix(text: str, overlap: int) -> str:
    # Only carry a sentence-clean tail into the next chunk. Punctuation-free
    # source lines (slide bullets, table/OCR fragments) have no such cut point;
    # falling back to the raw tail there would duplicate that exact fragment
    # into every following chunk until a period finally appears (it can chain
    # for dozens of chunks in slide decks and OCR'd tables).
    if overlap <= 0:
        return ""
    window = text if len(text) <= overlap else text[-overlap:]
    tail = window.strip()
    cut = tail.find("。")
    if cut >= 0 and cut < len(tail) - 1:
        return tail[cut + 1 :].strip()
    return ""


def _contextualize(body: str, heading: str | None) -> str:
    if not heading:
        return body
    if body.startswith(heading):
        return body
    return f"{heading}\n{body}"


def _chunk_title(body: str, heading: str | None) -> str:
    if heading:
        return heading[:_TITLE_MAX_LEN]
    first_line = body.strip().splitlines()[0].strip()
    if not first_line:
        return "未命名切片"
    if len(first_line) <= _TITLE_MAX_LEN:
        return first_line
    return f"{first_line[:_TITLE_MAX_LEN].rstrip('，,;；')}…"


def _section_label(heading: str | None, page: str | None) -> str | None:
    if heading and page:
        return f"第{page}页 · {heading}"
    if heading:
        return heading
    if page:
        return f"第{page}页"
    return None
