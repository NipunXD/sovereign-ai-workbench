"""Scrubbing sensitive values before they reach the audit log or structured logs.

The audit trail is meant to be readable by auditors who are not cleared for the
document contents themselves. It records *that* something happened and to which
resource, never the confidential payload. This module is the last line of
defence for values that slip into metadata by accident.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

#: Keys whose values are never recorded, matched case-insensitively as substrings.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "cookie",
        "session_key",
        "refresh_token",
        "access_token",
        "private_key",
        "jwt",
        "credential",
        "salt",
        "password_hash",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer tokens and JWTs
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.I), f"Bearer {REDACTED}"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"), REDACTED),
    # Indian PAN and Aadhaar, which do appear in HR-adjacent correspondence
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), REDACTED),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), REDACTED),
    # Email addresses and long digit runs (card/account numbers)
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), REDACTED),
    (re.compile(r"\b\d{12,19}\b"), REDACTED),
)

#: Beyond this, metadata is truncated — the audit log stores digests, not bodies.
MAX_STRING = 512


def redact_text(text: str) -> str:
    """Mask sensitive patterns inside a free-text string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact a structure for safe persistence.

    Deeply nested structures are collapsed rather than walked forever; anything
    past a reasonable depth is not audit-relevant detail anyway.
    """
    if _depth > 8:
        return "[TRUNCATED: max depth]"

    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(str(key)) else redact(val, _depth=_depth + 1)
            for key, val in value.items()
        }
    if isinstance(value, list | tuple):
        if len(value) > 100:
            head = [redact(v, _depth=_depth + 1) for v in list(value)[:100]]
            return [*head, f"[TRUNCATED: {len(value) - 100} more items]"]
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        cleaned = redact_text(value)
        if len(cleaned) > MAX_STRING:
            return f"{cleaned[:MAX_STRING]}... [TRUNCATED: {len(cleaned)} chars]"
        return cleaned
    if isinstance(value, bytes):
        return f"[BYTES: {len(value)}]"
    return value
