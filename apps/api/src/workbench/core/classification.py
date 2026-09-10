"""Document sensitivity levels.

A pure value object with no dependencies. It lives in ``core`` rather than
beside the ORM models because the retrieval path, the RBAC filter and the
ingestion pipeline all need it, and none of them should have to import
SQLAlchemy to ask whether ``confidential`` outranks ``internal``.
"""

from __future__ import annotations

from typing import Final


class Classification:
    """The sensitivity ladder. Order is significant."""

    PUBLIC: Final = "public"
    INTERNAL: Final = "internal"
    CONFIDENTIAL: Final = "confidential"
    RESTRICTED: Final = "restricted"

    ORDER: Final[list[str]] = [PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED]

    @classmethod
    def rank(cls, value: str) -> int:
        """Position on the ladder. Unknown labels rank above everything.

        Failing closed is the only safe default: a typo in a classification
        label must hide a document, never expose one.
        """
        try:
            return cls.ORDER.index(value)
        except ValueError:
            return len(cls.ORDER)

    @classmethod
    def clearance_rank(cls, clearance: str) -> int:
        """Position of a *clearance*. Unknown clearances rank below everything.

        This is deliberately the mirror image of :meth:`rank`. For a document
        label, "unrecognised" must mean *maximally sensitive* so it stays
        hidden. For a clearance, "unrecognised" must mean *no access at all* —
        using one function for both would hand an unauthenticated caller the
        highest clearance in the system.
        """
        try:
            return cls.ORDER.index(clearance)
        except ValueError:
            return -1

    @classmethod
    def at_or_below(cls, clearance: str) -> list[str]:
        """Every classification a holder of this clearance may retrieve."""
        return cls.ORDER[: cls.clearance_rank(clearance) + 1]

    @classmethod
    def outranks(cls, value: str, clearance: str) -> bool:
        """Whether ``value`` is too sensitive for ``clearance``."""
        return cls.rank(value) > cls.clearance_rank(clearance)
