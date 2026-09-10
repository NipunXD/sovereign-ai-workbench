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
