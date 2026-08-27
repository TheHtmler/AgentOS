from agent_api.knowledge.chunking import chunk_text


def test_chunk_text_packs_short_paragraphs() -> None:
    chunks = chunk_text("第一段内容这里够长。\n\n第二段内容这里也够长。", max_chars=1200)
    assert len(chunks) == 1
    assert "第一段" in chunks[0].content
    assert "第二段" in chunks[0].content


def test_chunk_text_keeps_heading_and_overlap() -> None:
    first = "发热、呕吐、拒食时要尽快联系代谢专科。" * 8
    second = "不要长时间禁食，按既定应急方案就医。" * 8
    chunks = chunk_text(
        f"## 急性失代偿\n\n{first}\n\n{second}",
        max_chars=180,
        overlap=40,
    )
    assert len(chunks) >= 2
    assert chunks[0].title == "急性失代偿"
    assert chunks[0].section_label == "急性失代偿"
    assert chunks[0].content.startswith("急性失代偿")
    assert any("应急方案" in chunk.content for chunk in chunks)


def test_chunk_text_merges_hard_wrapped_lines() -> None:
    chunks = chunk_text("甲基丙二酸血症是\n一类有机酸代谢病，\n可在新生儿期发病。")
    assert len(chunks) == 1
    assert "甲基丙二酸血症是一类有机酸代谢病，可在新生儿期发病。" in chunks[0].content


def test_chunk_text_uses_page_markers() -> None:
    chunks = chunk_text("[第 3 页]\nC3 升高提示有机酸代谢异常可能，需要进一步鉴别 MMA 与 PA。")
    assert chunks[0].section_label == "第3页"
    assert "C3 升高" in chunks[0].content


def test_chunk_text_hard_splits_long_paragraph() -> None:
    body = "句。" * 400
    chunks = chunk_text(body, max_chars=100, overlap=0)
    assert len(chunks) >= 2
    assert all(len(chunk.content) <= 120 for chunk in chunks)


def test_chunk_text_overlap_skips_when_no_sentence_boundary() -> None:
    # Slide/table/OCR fragments without "。" gave the old fallback nothing
    # clean to cut, so it duplicated the raw tail into every following
    # chunk. Punctuation-free pieces should now start fresh instead.
    fragments = "\n\n".join(f"要点{i}没有句号仅是短语" for i in range(30))
    chunks = chunk_text(fragments, max_chars=60, overlap=40)
    assert len(chunks) >= 3
    bodies = [chunk.content for chunk in chunks]
    assert len(bodies) == len(set(bodies))


def test_chunk_text_empty_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_text("   \n\n  ")
