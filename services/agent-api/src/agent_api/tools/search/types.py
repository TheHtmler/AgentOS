from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    provider: str
    query: str
    results: list[SearchResult]


class SearchProviderError(Exception):
    """Provider-level failure; recoverable errors may trigger router failover."""

    def __init__(self, message: str, *, provider: str, recoverable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.recoverable = recoverable
