"""Server-sent events for streaming agent traces.

SSE rather than WebSockets: the traffic is entirely server-to-client, SSE
survives proxies that mangle upgrades, it reconnects with ``Last-Event-ID``
built in, and it can be debugged with ``curl``. None of that is true of a
WebSocket, and nothing here needs bidirectional messaging.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from workbench.core.event_names import EventName as EventName
from workbench.core.events import Event, EventBus
from workbench.core.hashing import canonical_json
from workbench.core.logging import get_logger

log = get_logger(__name__)

#: Comment frames keep intermediaries from closing an idle connection while a
#: model is thinking. A long agentic step can be silent for a while.
HEARTBEAT_INTERVAL_S = 15.0


def format_sse(name: str, data: dict[str, Any], *, seq: int | None = None) -> str:
    """Render one SSE frame."""
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {name}")
    lines.append(f"data: {canonical_json(data)}")
    return "\n".join(lines) + "\n\n"


async def with_heartbeat(
    frames: AsyncIterator[str], *, interval_s: float = HEARTBEAT_INTERVAL_S
) -> AsyncIterator[str]:
    """Interleave comment frames into a stream that may go quiet.

    An agent run has long legitimate silences — a local model thinking, a
    90-second tool argument generation, and above all the wait for a human to
    approve a document, which is silent for three minutes by design. Nothing
    distinguishes those from a dead connection except traffic, so the server
    has to produce some.

    Without this the silence is indistinguishable from a hang at both ends: a
    proxy may reap the connection, and the browser's own stall detection fires
    on a run that is working perfectly and merely waiting for a person.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)

    async def pump() -> None:
        try:
            async for frame in frames:
                await queue.put(frame)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=interval_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if frame is None:
                return
            yield frame
    finally:
        task.cancel()


async def event_stream(
    bus: EventBus,
    run_id: str,
    *,
    last_event_id: int = 0,
    heartbeat_s: float = HEARTBEAT_INTERVAL_S,
) -> AsyncIterator[str]:
    """Yield SSE frames for a run until it finishes or the client goes away.

    Replays anything after ``last_event_id`` first, so a browser that dropped
    mid-run resumes rather than losing the middle of a trace.
    """
    queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=256)

    async def pump() -> None:
        try:
            async for event in bus.subscribe(run_id, after_seq=last_event_id):
                await queue.put(event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                # A comment frame: valid SSE, ignored by EventSource, and enough
                # to stop a proxy reaping a connection during a long think.
                yield ": ping\n\n"
                continue
            if event is None:
                return
            yield format_sse(event.name, event.data, seq=event.seq)
    except asyncio.CancelledError:
        log.debug("sse_client_disconnected", run_id=run_id)
        raise
    finally:
        task.cancel()


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Tells nginx not to buffer, which would otherwise defeat streaming
    # entirely and make every answer appear at once at the end.
    "X-Accel-Buffering": "no",
}
