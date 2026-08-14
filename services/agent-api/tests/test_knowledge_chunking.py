from agent_api.knowledge.chunking import chunk_text


def test_chunk_text_splits_on_blank_lines() -> None:
    chunks = chunk_text("第一段内容这里够长。\n\n第二段内容这里也够长。", max_chars=1200)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert "第一段" in chunks[0].content


def test_chunk_text_hard_splits_long_paragraph() -> None:
    body = "句。" * 400
    chunks = chunk_text(body, max_chars=100)
    assert len(chunks) >= 2
    assert all(len(c.content) <= 120 for c in chunks)


def test_chunk_text_empty_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_text("   \n\n  ")
