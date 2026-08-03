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
    assert response.outline.startswith("# One")


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
