"""In-process per-run event fan-out for streaming HITL resume phases.

The resumed run executes in a background task decoupled from any HTTP
response, so browsers subscribe via ``GET /v1/runs/{id}/stream``. A small
replay buffer per run covers the gap between ``POST /resume`` starting the
background continuation and the browser attaching its stream — early events
(RUN_STARTED, the first tool calls) must not be lost in that window.

This is deliberately not a general event bus: one publisher per run, all
methods are synchronous (called only from the event loop), and all state for
a run is dropped on close.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from ag_ui.core import BaseEvent


class RunEventBroker:
    """Fan out one run's AG-UI events to zero or more SSE subscribers."""

    def __init__(self) -> None:
        self._buffers: dict[UUID, list[BaseEvent]] = {}
        self._subscribers: dict[UUID, set[asyncio.Queue[BaseEvent | None]]] = {}

    def open(self, run_id: UUID) -> None:
        """Mark a run as publishing before its background task starts.

        Called synchronously by the resume endpoint path so a subscriber
        attaching immediately after the HTTP response always finds a
        publisher, even before the first event exists.
        """

        self._buffers.setdefault(run_id, [])
        self._subscribers.setdefault(run_id, set())

    def has_publisher(self, run_id: UUID) -> bool:
        return run_id in self._buffers

    def publish(self, run_id: UUID, event: BaseEvent) -> None:
        buffer = self._buffers.get(run_id)
        if buffer is None:
            return
        buffer.append(event)
        for queue in self._subscribers.get(run_id, ()):
            queue.put_nowait(event)

    def subscribe(self, run_id: UUID) -> asyncio.Queue[BaseEvent | None] | None:
        """Attach a subscriber; buffered events are replayed into its queue."""

        buffer = self._buffers.get(run_id)
        if buffer is None:
            return None
        queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue()
        for event in buffer:
            queue.put_nowait(event)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: UUID, queue: asyncio.Queue[BaseEvent | None]) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(queue)

    def close(self, run_id: UUID) -> None:
        """End the stream for all subscribers and drop the run's state."""

        for queue in self._subscribers.pop(run_id, ()):
            queue.put_nowait(None)
        self._buffers.pop(run_id, None)
