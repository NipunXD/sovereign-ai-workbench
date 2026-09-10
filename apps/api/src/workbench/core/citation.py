"""The citation record.

Lives in ``core`` rather than beside the resolver in ``rag`` because two
layers need it and neither may import the other: retrieval produces citations,
and the artifact builders write them into the provenance page of every
generated document. Putting the shared type at the bottom is what lets both
depend on it without depending on each other.

``rag.citations`` re-exports it, so existing imports keep working and the
resolver still reads as the module that owns the concept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from workbench.core.ir import BBox


@dataclass(frozen=True, slots=True)
class Citation:
    """One resolved reference, carrying everything the UI needs to show it."""

    n: int
    chunk_id: str
    doc_id: str
    doc_title: str
    page_no: int
    bbox: BBox = field(default_factory=BBox)
    snippet: str = ""
    section_path: list[str] = field(default_factory=list)
    score: float = 0.0
    retrieval_method: str = "hybrid"
    #: Inherited from the source text's OCR confidence. Surfaced so a reader can
    #: see when an answer rests on poorly-recognised text.
    confidence: float = 1.0
    doc_type: str = ""
    parent_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Citation:
        """Rebuild a citation from :meth:`as_dict`.

        Needed because a citation now has to survive a trip through the
        database: an approval carries the run's provenance so the document can
        be produced after the run has gone, and JSONB stores dictionaries, not
        dataclasses. Unknown keys are ignored rather than raising, so a record
        written by an older version still loads.
        """
        return cls(
            n=int(data.get("n", 0)),
            chunk_id=str(data.get("chunk_id", "")),
            doc_id=str(data.get("doc_id", "")),
            doc_title=str(data.get("doc_title", "")),
            page_no=int(data.get("page_no", 1)),
            bbox=BBox(**(data.get("bbox") or {})),
            snippet=str(data.get("snippet", "")),
            section_path=list(data.get("section_path") or []),
            score=float(data.get("score", 0.0)),
            retrieval_method=str(data.get("retrieval_method", "hybrid")),
            confidence=float(data.get("confidence", 1.0)),
            doc_type=str(data.get("doc_type", "")),
            parent_id=data.get("parent_id"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "doc_type": self.doc_type,
            "page_no": self.page_no,
            "bbox": self.bbox.as_dict(),
            "snippet": self.snippet,
            "section_path": self.section_path,
            "score": round(self.score, 4),
            "retrieval_method": self.retrieval_method,
            "confidence": round(self.confidence, 3),
        }
