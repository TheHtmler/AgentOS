from typing import Protocol

from agent_api.tools.fetch.types import FetchResponse


class FetchProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def fetch(
        self,
        url: str,
        *,
        max_chars: int,
        timeout: float,
    ) -> FetchResponse: ...
