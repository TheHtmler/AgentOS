from agent_api.tools.fetch.types import FetchResponse


def apply_fetch_limits(
    *,
    provider: str,
    url: str,
    title: str,
    outline: str,
    text: str,
    max_chars: int,
) -> FetchResponse:
    """Keep outline preferred and hard-truncate body text for local context budgets."""

    normalized_title = title.strip()
    normalized_outline = outline.strip()
    normalized_text = text.strip()
    total_chars = len(normalized_text)
    truncated = total_chars > max_chars
    body = normalized_text[:max_chars] if truncated else normalized_text

    # Reserve outline room; if outline alone is huge, clip it too.
    outline_budget = max(500, min(2_000, max_chars // 4))
    if len(normalized_outline) > outline_budget:
        normalized_outline = normalized_outline[:outline_budget].rstrip() + "…"

    return FetchResponse(
        provider=provider,
        url=url,
        title=normalized_title,
        outline=normalized_outline,
        text=body,
        truncated=truncated,
        total_chars=total_chars,
    )
