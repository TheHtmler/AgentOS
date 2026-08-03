from typing import Protocol

from agent_api.tools.search.types import SearchResponse


class SearchProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
    ) -> SearchResponse: ...
