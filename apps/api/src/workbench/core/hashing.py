"""Canonical hashing.

The audit chain and artifact provenance both depend on hashing a structure the
exact same way every time, on every machine. That means sorted keys, no
insignificant whitespace, and a defined representation for the types JSON does
not cover natively.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

#: Marks the first link of a hash chain, where there is no predecessor.
GENESIS_HASH = "0" * 64


def _default(value: Any) -> Any:
    """Render types json.dumps cannot handle, stably."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID | Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(value, key=repr)
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialise to the one representation this system considers canonical."""
    return json.dumps(
        value,
        default=_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON form, as lowercase hex."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(data: bytes) -> str:
    """SHA-256 of raw bytes — used for uploaded blobs and generated artifacts."""
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file, streamed so a 200 MB upload does not enter memory."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def chain_hash(payload: Any, prev_hash: str) -> str:
    """Link a record to its predecessor.

    Altering any earlier record changes every hash after it, so tampering is
    detectable at the exact row where it happened.
    """
    return hashlib.sha256(f"{canonical_json(payload)}|{prev_hash}".encode()).hexdigest()
