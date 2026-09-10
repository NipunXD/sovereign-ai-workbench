"""Injectable clock.

Time is a dependency like any other. Threading it through explicitly means the
eval harness and golden-file tests can freeze it, which is what makes generated
artifacts byte-for-byte reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

#: The instant every deterministic run pretends it is. Arbitrary but fixed.
FROZEN_INSTANT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Real wall-clock time, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock that does not move, for tests and deterministic mode."""

    def __init__(self, instant: datetime = FROZEN_INSTANT) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def advance(self, seconds: float) -> None:
        """Move the clock forward, for tests that assert on elapsed time."""
        from datetime import timedelta

        self._instant += timedelta(seconds=seconds)


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    """Replace the process clock. Used by app startup and test fixtures."""
    global _clock
    _clock = clock


def now() -> datetime:
    """Current time according to the active clock."""
    return _clock.now()
