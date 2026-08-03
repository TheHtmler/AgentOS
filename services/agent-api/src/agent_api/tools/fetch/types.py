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


class FetchProviderError(Exception):
    """Provider-level failure; recoverable errors may trigger router failover."""

    def __init__(self, message: str, *, provider: str, recoverable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.recoverable = recoverable
