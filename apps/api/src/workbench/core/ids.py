"""Identifier generation.

ULIDs rather than UUID4: they sort lexicographically by creation time, so
``ORDER BY id`` is chronological, primary-key inserts stay append-friendly, and
a trace read in the raw database is legible in the order things happened.
"""

from __future__ import annotations

import itertools
from typing import Final

from ulid import ULID

from workbench.core.clock import now

#: Deterministic mode replaces random ULIDs with a predictable sequence so that
#: golden files and artifact digests are stable across runs.
_deterministic = False
_counter = itertools.count(1)

_PREFIXES: Final[dict[str, str]] = {
    "user": "usr",
    "session": "ses",
    "conversation": "cnv",
    "message": "msg",
    "run": "run",
    "step": "stp",
    "document": "doc",
    "chunk": "chk",
    "job": "job",
    "artifact": "art",
    "approval": "apr",
    "execution": "exe",
    "audit": "aud",
    "eval": "evl",
}


def set_deterministic(enabled: bool) -> None:
    """Switch ID generation between random and reproducible."""
    global _deterministic, _counter
    _deterministic = enabled
    _counter = itertools.count(1)


def new_id() -> str:
    """A fresh sortable identifier."""
    if _deterministic:
        return f"00000000000000000000000{next(_counter):09d}"[-26:]
    return str(ULID.from_datetime(now()))


def prefixed_id(kind: str) -> str:
    """A namespaced identifier such as ``run_01JG...``.

    The prefix costs four characters and saves an enormous amount of confusion
    when identifiers appear in logs, traces and support tickets.
    """
    prefix = _PREFIXES.get(kind, kind[:3])
    return f"{prefix}_{new_id()}"
