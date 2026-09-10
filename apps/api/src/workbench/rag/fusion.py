"""Reciprocal Rank Fusion.

Dense and sparse retrieval fail in complementary ways. An embedding blurs exact
tokens — ``V-1201`` and ``V-1202`` land near each other, which is precisely
wrong for equipment tags. Keyword search has the opposite problem: it finds
``V-1201`` exactly but misses "the vessel" entirely.

RRF combines them by *rank* rather than score, which matters because cosine
similarity and BM25 relevance are not on comparable scales and normalising them
against each other requires tuning that does not survive a corpus change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: The RRF constant. 60 is the value from the original paper and is deliberately
#: large: it flattens the contribution of rank position so that being 1st rather
#: than 3rd in one retriever does not dominate appearing in both.
DEFAULT_RRF_K = 60


class Ranked(Protocol):
    """Anything with a stable identity that can be ranked."""

    @property
    def chunk_id(self) -> str: ...


@dataclass
class FusedHit:
    """One result, with the provenance of how it was found."""

    chunk_id: str
    score: float
    #: Rank in each retriever that returned it, for the trace panel.
    ranks: dict[str, int] = field(default_factory=dict)
    payloads: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def method(self) -> str:
        """How this result was found — shown on the citation."""
        sources = sorted(self.ranks)
        if len(sources) > 1:
            return "hybrid"
        return sources[0] if sources else "unknown"

    @property
    def found_by_both(self) -> bool:
        return len(self.ranks) > 1


def reciprocal_rank_fusion(
    rankings: dict[str, Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: dict[str, float] | None = None,
    limit: int | None = None,
) -> list[FusedHit]:
    """Fuse several ranked ID lists into one.

    ``rankings`` maps a retriever name to its results in rank order. Each
    contributes ``weight / (k + rank)`` to an item's score, so a document found
    by both retrievers outranks one found brilliantly by only one — which is the
    behaviour that makes hybrid search worth the extra query.
    """
    weights = weights or {}
    fused: dict[str, FusedHit] = {}

    for source, ordered in rankings.items():
        weight = weights.get(source, 1.0)
        for position, chunk_id in enumerate(ordered, start=1):
            hit = fused.get(chunk_id)
            if hit is None:
                hit = FusedHit(chunk_id=chunk_id, score=0.0)
                fused[chunk_id] = hit
            hit.score += weight / (k + position)
            hit.ranks[source] = position

    results = sorted(fused.values(), key=lambda h: (-h.score, h.chunk_id))
    return results[:limit] if limit else results


def apply_confidence_penalty(
    hits: list[FusedHit],
    confidences: dict[str, float],
    *,
    penalty: float = 0.15,
    floor: float = 0.8,
) -> list[FusedHit]:
    """Demote passages that came from poorly-recognised text.

    A chunk read from a bad scan at 55% confidence may say anything. It is not
    excluded — the information is often still the only record that exists — but
    it should lose to a clean source that says the same thing.
    """
    for hit in hits:
        confidence = confidences.get(hit.chunk_id, 1.0)
        if confidence < floor:
            shortfall = (floor - confidence) / floor
            hit.score *= 1.0 - (penalty * shortfall)
    return sorted(hits, key=lambda h: (-h.score, h.chunk_id))
