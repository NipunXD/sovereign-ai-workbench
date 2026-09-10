"""In-process publish/subscribe for streaming agent traces to SSE clients.

An agent run publishes events as it works; the HTTP handler serving
``POST /chat/stream`` subscribes to that run and forwards them to the browser.

Two properties matter and are easy to get wrong:

* **A slow client must not stall the agent.** Each subscriber has a bounded
  queue. If a browser tab is suspended and stops reading, its queue fills and
  its oldest events are dropped rather than applying backpressure to the run.
* **Events must survive a reconnect.** Every run keeps a bounded replay buffer
  keyed by monotonic sequence number, so a client that drops can resume from
  ``Last-Event-ID`` instead of losing the middle of a trace.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from workbench.core.clock import now
from workbench.core.logging import get_logger

log = get_logger(__name__)

#: Events buffered per run for reconnecting clients. A long agentic run emits a
#: few hundred events, so this holds a complete trace comfortably.
REPLAY_BUFFER_SIZE = 1000

#: Per-subscriber queue depth before the oldest events are dropped.
SUBSCRIBER_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class Event:
    """One item in a run's trace stream."""

    seq: int
    name: str
    data: dict[str, Any]
    run_id: str
    timestamp: str = field(default_factory=lambda: now().isoformat())

    def to_sse(self) -> str:
        """Render as an SSE frame, including the id used for resumption."""
        from workbench.core.hashing import canonical_json

        return f"id: {self.seq}\nevent: {self.name}\ndata: {canonical_json(self.data)}\n\n"


class _RunChannel:
    """The fan-out point for a single run."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event | None]] = set()
        self._replay: deque[Event] = deque(maxlen=REPLAY_BUFFER_SIZE)
        self._seq = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def publish(self, event: Event) -> None:
        self._replay.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event for this subscriber only. The run keeps
                # going; the client can repair its view via the replay buffer.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.warning("sse_subscriber_overflow", seq=event.seq)

    def subscribe(self, after_seq: int = 0) -> asyncio.Queue[Event | None]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        # Replay anything the client missed before live events start arriving.
        for event in self._replay:
            if event.seq > after_seq:
                with_room = queue.qsize() < SUBSCRIBER_QUEUE_SIZE
                if with_room:
                    queue.put_nowait(event)
        if self._closed:
            queue.put_nowait(None)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        self._subscribers.discard(queue)

    def close(self) -> None:
        self._closed = True
        for queue in self._subscribers:
            with_room = queue.qsize() < SUBSCRIBER_QUEUE_SIZE
            if with_room:
                queue.put_nowait(None)

    def replay(self, after_seq: int = 0) -> list[Event]:
        return [event for event in self._replay if event.seq > after_seq]


class EventBus:
    """Routes trace events from agent runs to their SSE subscribers."""

    def __init__(self) -> None:
        self._channels: dict[str, _RunChannel] = {}

    def _channel(self, run_id: str) -> _RunChannel:
        channel = self._channels.get(run_id)
        if channel is None:
            channel = _RunChannel()
            self._channels[run_id] = channel
        return channel

    async def publish(self, run_id: str, name: str, data: dict[str, Any]) -> Event:
        """Emit one event for a run."""
        channel = self._channel(run_id)
        event = Event(seq=channel.next_seq(), name=name, data=data, run_id=run_id)
        channel.publish(event)
        return event

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[Event]:
        """Yield events for a run until it finishes or the client disconnects."""
        channel = self._channel(run_id)
        queue = channel.subscribe(after_seq)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            channel.unsubscribe(queue)

    def replay(self, run_id: str, after_seq: int = 0) -> list[Event]:
        """Events already emitted for a run, for a non-streaming trace fetch."""
        channel = self._channels.get(run_id)
        return channel.replay(after_seq) if channel else []

    async def close(self, run_id: str) -> None:
        """Signal that a run has finished, ending every subscriber's stream."""
        channel = self._channels.get(run_id)
        if channel:
            channel.close()

    def discard(self, run_id: str) -> None:
        """Release a finished run's buffer.

        Called once the trace has been persisted to ``agent_steps``, which is
        the durable record; the in-memory buffer only serves live reconnects.
        """
        self._channels.pop(run_id, None)

    @property
    def active_runs(self) -> int:
        return sum(1 for channel in self._channels.values() if not channel.closed)


_bus = EventBus()


def get_event_bus() -> EventBus:
    """The process-wide event bus."""
    return _bus
