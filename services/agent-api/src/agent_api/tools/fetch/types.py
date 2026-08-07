from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FetchResponse:
    provider: str
    url: str
    title: str
    outline: str
    text: str
    truncated: bool
    total_chars: int
    # Full extracted body for Artifact persistence; never sent to the model.
    full_text: str = ""
    artifact_id: str | None = None

    def to_tool_payload(
        self,
        *,
        artifact_preview_chars: int = 1_000,
        artifact_outline_chars: int = 600,
    ) -> dict[str, object]:
        """JSON shape returned to the model (excludes full_text).

        When an Artifact was stored, shrink outline/text further so local 8k-context
        models are not flooded by the first tool result.
        """

        outline = self.outline
        text = self.text
        truncated = self.truncated
        if self.artifact_id:
            if len(outline) > artifact_outline_chars:
                outline = outline[:artifact_outline_chars].rstrip() + "…"
            if len(text) > artifact_preview_chars:
                text = text[:artifact_preview_chars].rstrip()
                truncated = True

        payload: dict[str, object] = {
            "provider": self.provider,
            "url": self.url,
            "title": self.title,
            "outline": outline,
            "text": text,
            "truncated": truncated,
            "total_chars": self.total_chars,
        }
        if self.artifact_id:
            payload["artifact_id"] = self.artifact_id
            payload["next_offset"] = len(text) if truncated else None
            payload["hint"] = (
                "Full page stored as Artifact. Call read_artifact with artifact_id "
                "and offset/next_offset for more text; do not raise fetch max_chars."
            )
        return payload


class FetchProviderError(Exception):
    """Provider-level failure; recoverable errors may trigger router failover."""

    def __init__(self, message: str, *, provider: str, recoverable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.recoverable = recoverable
