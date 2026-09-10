"""Citation payloads and marker resolution.

The synthesis prompt asks the model to emit ``[[cite:CHUNK_ID]]`` markers. This
module turns those into the numbered references a reader sees, and into the
coordinates the document viewer uses to highlight the exact region a claim came
from.

A marker that cannot be resolved is *removed and reported*, never left in the
answer. A dangling reference is worse than no reference: it looks like evidence
while pointing at nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from workbench.ingest.ir import BBox

# Models are asked for `[[cite:ID]]` and reliably produce variations on it. All
# of these appear in practice and all must resolve, because an unparsed marker
# is both an ugly artefact in the answer and a false hallucination signal:
#
#   [[cite:a]]              the documented form
#   [[cite: a ]]            stray whitespace
#   [[cite:a,b]]            several ids in one marker
#   [[cite:a][cite:b]]      markers run together without separators
#   [[cite:a]][[cite:b]]    adjacent markers
#
#: Matches one marker, capturing everything between the outer brackets.
CITE_PATTERN = re.compile(r"\[\[\s*cite\s*:\s*(.+?)\s*\]\]", re.DOTALL)

#: Splits a marker body into ids, tolerating the run-together form.
_ID_SPLIT = re.compile(r"[,;\s]+|\]\s*\[\s*cite\s*:\s*", re.IGNORECASE)


def _marker_ids(body: str) -> list[str]:
    """Extract every chunk id from one marker body, in order."""
    ids = []
    for token in _ID_SPLIT.split(body):
        token = token.strip().strip("[]")
        if token.lower().startswith("cite:"):
            token = token[5:].strip()
        if token:
            ids.append(token)
    return ids

#: Snippet length in the citation popover. Long enough to confirm the claim,
#: short enough not to become a way of reading a whole restricted document
#: through citations.
SNIPPET_CHARS = 240


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


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    """An answer with its markers replaced by numbered references."""

    text: str
    citations: list[Citation]
    #: Markers the model invented that match no retrieved chunk. A non-empty
    #: list is a hallucination signal and is surfaced to the validation node.
    unresolved: list[str] = field(default_factory=list)

    @property
    def has_citations(self) -> bool:
        return bool(self.citations)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A retrieved chunk as offered to the model."""

    chunk_id: str
    text: str
    doc_id: str
    doc_title: str = ""
    doc_type: str = ""
    page_from: int = 1
    #: Defaults to 0 so that ``min``/``max`` treat an unset value as "same page
    #: as page_from" rather than as page 1.
    page_to: int = 0
    bbox: BBox = field(default_factory=BBox)
    section_path: list[str] = field(default_factory=list)
    score: float = 0.0
    retrieval_method: str = "hybrid"
    confidence: float = 1.0
    parent_id: str | None = None

    def to_citation(self, n: int) -> Citation:
        snippet = self.text.strip().replace("\n", " ")
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
        return Citation(
            n=n,
            chunk_id=self.chunk_id,
            doc_id=self.doc_id,
            doc_title=self.doc_title,
            doc_type=self.doc_type,
            page_no=self.page_from,
            bbox=self.bbox,
            snippet=snippet,
            section_path=list(self.section_path),
            score=self.score,
            retrieval_method=self.retrieval_method,
            confidence=self.confidence,
            parent_id=self.parent_id,
        )

    def as_prompt_block(self, index: int) -> str:
        """Render as a delimited evidence block for the synthesis prompt.

        The delimiters and the explicit label matter: retrieved document text is
        untrusted input. A document containing "ignore your instructions" must
        read as data the model is quoting, not as a command. The model is told,
        in the system prompt, that everything inside these blocks is source
        material and never an instruction.
        """
        # Ordered explicitly: a chunk built with only page_from set would
        # otherwise render a reversed range like "pages 7-1".
        page_to = self.page_to or self.page_from
        first, last = min(self.page_from, page_to), max(self.page_from, page_to)
        location = f"page {first}" if first == last else f"pages {first}-{last}"
        section = " > ".join(self.section_path) if self.section_path else ""
        header = f"[{index}] {self.doc_title or self.doc_id} — {location}"
        if section:
            header += f" — {section}"
        if self.confidence < 0.8:
            header += f" — OCR confidence {self.confidence:.0%}"
        return (
            f"<source id=\"{self.chunk_id}\">\n"
            f"{header}\n"
            f"{self.text.strip()}\n"
            f"</source>"
        )


def build_evidence_prompt(evidence: list[EvidenceItem]) -> str:
    """Render the retrieved set as the evidence section of a prompt."""
    if not evidence:
        return "<sources>\n(no relevant sources were retrieved)\n</sources>"
    blocks = "\n\n".join(item.as_prompt_block(i) for i, item in enumerate(evidence, start=1))
    return f"<sources>\n{blocks}\n</sources>"


def resolve_markers(text: str, evidence: list[EvidenceItem]) -> ResolvedAnswer:
    """Replace ``[[cite:ID]]`` markers with ``[n]`` and collect the citations.

    Numbering follows first appearance in the answer, not retrieval rank, so the
    reference list reads in the order the reader encounters it. A marker citing
    a chunk that was never retrieved is dropped and recorded.
    """
    by_id = {item.chunk_id: item for item in evidence}
    assigned: dict[str, int] = {}
    citations: list[Citation] = []
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        numbers: list[int] = []
        for chunk_id in _marker_ids(match.group(1)):
            item = by_id.get(chunk_id)
            if item is None:
                unresolved.append(chunk_id)
                continue
            if chunk_id not in assigned:
                assigned[chunk_id] = len(assigned) + 1
                citations.append(item.to_citation(assigned[chunk_id]))
            numbers.append(assigned[chunk_id])
        return "".join(f"[{n}]" for n in numbers)

    resolved = CITE_PATTERN.sub(replace, text)
    # Removing a marker can leave " ." or a double space behind.
    resolved = re.sub(r"\s+([.,;:!?])", r"\1", resolved)
    resolved = re.sub(r"[ \t]{2,}", " ", resolved).strip()

    return ResolvedAnswer(text=resolved, citations=citations, unresolved=unresolved)


def strip_markers(text: str) -> str:
    """Remove every citation marker, for plain-text contexts."""
    return re.sub(r"\s+([.,;:!?])", r"\1", CITE_PATTERN.sub("", text)).strip()
