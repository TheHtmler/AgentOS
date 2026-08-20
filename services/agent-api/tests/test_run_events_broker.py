"""Unit tests for the per-run resume event fan-out (no database needed)."""

import asyncio
from uuid import uuid4

import pytest
from ag_ui.core import CustomEvent, RunStartedEvent

from agent_api.run_events_broker import RunEventBroker


@pytest.mark.anyio
async def test_broker_replays_buffer_and_fans_out() -> None:
    broker = RunEventBroker()
    run_id = uuid4()
    broker.open(run_id)
    assert broker.has_publisher(run_id)

    first = RunStartedEvent(thread_id="thread-1", run_id=str(run_id))
    broker.publish(run_id, first)

    queue = broker.subscribe(run_id)
    assert queue is not None
    # Events published before subscribing arrive via the replay buffer.
    assert queue.get_nowait() is first

    second = CustomEvent(name="agentos_keepalive", value=None)
    broker.publish(run_id, second)
    assert queue.get_nowait() is second

    # A second subscriber gets the full replay as well.
    other = broker.subscribe(run_id)
    assert other is not None
    assert other.get_nowait() is first
    assert other.get_nowait() is second

    broker.unsubscribe(run_id, other)
    broker.close(run_id)
    assert queue.get_nowait() is None
    # An unsubscribed queue is no longer fanned out to, not even the sentinel.
    with pytest.raises(asyncio.QueueEmpty):
        other.get_nowait()
    assert broker.subscribe(run_id) is None
    assert not broker.has_publisher(run_id)


@pytest.mark.anyio
async def test_broker_ignores_unknown_runs() -> None:
    broker = RunEventBroker()
    run_id = uuid4()
    # Publish/unsubscribe/close without open must be safe no-ops (e.g. a
    # resume that failed before its stream was opened).
    broker.publish(run_id, CustomEvent(name="x", value=None))
    broker.unsubscribe(run_id, asyncio.Queue())
    broker.close(run_id)
    assert broker.subscribe(run_id) is None
    assert not broker.has_publisher(run_id)
