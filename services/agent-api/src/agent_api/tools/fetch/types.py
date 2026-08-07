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

    def to_tool_payload(self) -> dict[str, object]:
        """JSON shape returned to the model (excludes full_text)."""

        payload: dict[str, object] = {
            "provider": self.provider,
            "url": self.url,
            "title": self.title,
            "outline": self.outline,
            "text": self.text,
            "truncated": self.truncated,
            "total_chars": self.total_chars,
        }
        if self.artifact_id:
            payload["artifact_id"] = self.artifact_id
        return payload


class FetchProviderError(Exception):
    """Provider-level failure; recoverable errors may trigger router failover."""

    def __init__(self, message: str, *, provider: str, recoverable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.recoverable = recoverable
