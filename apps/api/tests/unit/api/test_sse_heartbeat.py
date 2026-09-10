"""Heartbeats on a stream that goes quiet.

An agent run has long legitimate silences: a local model thinking, a
90-second tool argument generation, and above all the wait for a person to
approve a document, which is silent for three minutes by design.

Nothing distinguishes those from a dead connection except traffic. The chat
stream sent none — the constant existed and only the replay endpoint used it —
so the browser's stall detection aborted runs that were working perfectly and
merely waiting for an approver.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from workbench.api.sse import with_heartbeat


async def test_a_silent_stream_still_carries_traffic() -> None:
    async def slow() -> AsyncIterator[str]:
        await asyncio.sleep(0.25)  # stands in for the approval wait
        yield "event: answer\ndata: {}\n\n"

    frames = [frame async for frame in with_heartbeat(slow(), interval_s=0.05)]

    assert frames[-1].startswith("event: answer")
    assert any(f.startswith(":") for f in frames), "no heartbeat during the silence"
    assert len([f for f in frames if f.startswith(":")]) >= 3


async def test_a_busy_stream_is_not_padded() -> None:
    async def fast() -> AsyncIterator[str]:
        for i in range(5):
            yield f"event: token\ndata: {i}\n\n"

    frames = [frame async for frame in with_heartbeat(fast(), interval_s=5.0)]

    assert len(frames) == 5
    assert not any(f.startswith(":") for f in frames)


async def test_frames_keep_their_order_and_content() -> None:
    async def mixed() -> AsyncIterator[str]:
        yield "event: run_started\ndata: {}\n\n"
        await asyncio.sleep(0.12)
        yield "event: run_finished\ndata: {}\n\n"

    frames = [f async for f in with_heartbeat(mixed(), interval_s=0.05)]
    events = [f for f in frames if not f.startswith(":")]

    assert events == [
        "event: run_started\ndata: {}\n\n",
        "event: run_finished\ndata: {}\n\n",
    ]


async def test_the_stream_ends_when_the_source_does() -> None:
    async def empty() -> AsyncIterator[str]:
        return
        yield  # pragma: no cover

    frames = [f async for f in with_heartbeat(empty(), interval_s=0.05)]
    assert frames == []
