from agent_api.tools.fetch.truncate import apply_fetch_limits


def test_apply_fetch_limits_truncates_text_and_keeps_outline() -> None:
    response = apply_fetch_limits(
        provider="local",
        url="https://example.com",
        title="Demo",
        outline="# One\n## Two",
        text="x" * 50,
        max_chars=20,
    )

    assert response.truncated is True
    assert response.total_chars == 50
    assert len(response.text) == 20
    assert response.full_text == "x" * 50
    assert "full_text" not in response.to_tool_payload()
    assert response.outline.startswith("# One")


def test_to_tool_payload_shrinks_when_artifact_present() -> None:
    from dataclasses import replace

    from agent_api.tools.fetch.truncate import apply_fetch_limits

    base = apply_fetch_limits(
        provider="local",
        url="https://example.com",
        title="Demo",
        outline="# " + ("heading " * 80),
        text="y" * 5_000,
        max_chars=2_500,
    )
    with_artifact = replace(base, artifact_id="11111111-1111-1111-1111-111111111111")
    payload = with_artifact.to_tool_payload(
        artifact_preview_chars=1_000,
        artifact_outline_chars=600,
    )
    assert payload["artifact_id"]
    assert len(str(payload["text"])) <= 1_000
    assert len(str(payload["outline"])) <= 601
    assert payload["truncated"] is True
    assert payload["next_offset"] == len(str(payload["text"]))
    assert "full_text" not in payload


def test_apply_fetch_limits_no_truncate_when_short() -> None:
    response = apply_fetch_limits(
        provider="firecrawl",
        url="https://example.com",
        title="Demo",
        outline="# One",
        text="hello",
        max_chars=100,
    )

    assert response.truncated is False
    assert response.text == "hello"
    assert response.total_chars == 5
