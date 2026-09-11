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

from workbench.core.citation import Citation as Citation
from workbench.core.ir import BBox

# Models are asked for `[[cite:ID]]` and reliably produce variations on it. All
# of these appear in practice and all must resolve, because an unparsed marker
# is both an ugly artefact in the answer and a false hallucination signal:
#
#   [[cite:a]]              the documented form
#   [[cite: a ]]            stray whitespace
#   [[cite:a,b]]            several ids in one marker
#   [[cite:a][cite:b]]      markers run together without separators
#   [[cite:a]][[cite:b]]    adjacent markers
#   (cite:a)                parenthesised
#   [cite:a]                single brackets
#
# The bracket style is deliberately loose. A marker written `(cite:a)` used to
# match nothing at all, which is worse than matching wrongly: an unrecognised
# marker is not a marker, so it is never resolved *and* never reported as
# unresolved. The chunk id stayed in the prose for the reader to see, the
# grounding score came out as 0% on an answer that had cited every line, and
# the failure looked like the model refusing to cite rather than the parser
# refusing to read.
#
#: Matches one marker, capturing everything between the outer brackets.
CITE_PATTERN = re.compile(
    r"[\[\(]{1,2}\s*cite\s*:\s*(.+?)\s*[\]\)]{1,2}",
    re.DOTALL | re.IGNORECASE,
)

#: A reference by the number shown in the source header — `[3]`, or `[[3]]`
#: when the model doubles the brackets the way the id form does.
#:
#: This exists because the prompt carried two numbering schemes at once. Every
#: source block is headed `[1] Title — page 4`, and the rules asked for
#: `[[cite:the-id]]`. Shown a number and asked for a 26-character ULID, the
#: model used the number — and a bare `[1]` matched nothing, so the answer came
#: out full of references that resolved to nothing at all. That is the dangling
#: reference this module exists to prevent, arrived at from the other side: not
#: an invented id, but a real one the parser would not read.
#:
#: Citing by ordinal is also simply the more reliable ask. The list is right
#: there in the prompt and the number is one character; the id has to be
#: transcribed exactly.
ORDINAL_PATTERN = re.compile(r"\[{1,2}\s*(\d{1,2})\s*\]{1,2}")

#: Both forms, matched in one pass.
#:
#: One pass rather than two, because the output of the first is valid input to
#: the second and means something different. Rewriting `[[cite:chk_b]]` to
#: `[2]` and then running the ordinal pass re-read that `[2]` as "the second
#: source" — a correctly cited claim silently repointed at a document it never
#: came from, which is the one error this module must never make.
MARKER_PATTERN = re.compile(
    r"[\[\(]{1,2}\s*cite\s*:\s*(?P<ids>.+?)\s*[\]\)]{1,2}"
    r"|\[{1,2}\s*(?P<ordinal>\d{1,2})\s*\]{1,2}",
    re.DOTALL | re.IGNORECASE,
)

#: Splits a marker body into ids, tolerating the run-together form.
_ID_SPLIT = re.compile(r"[,;\s]+|[\]\)]\s*[\[\(]\s*cite\s*:\s*", re.IGNORECASE)


def _marker_ids(body: str) -> list[str]:
    """Extract every chunk id from one marker body, in order."""
    ids = []
    for token in _ID_SPLIT.split(body):
        token = token.strip().strip("[]()")
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
        return f'<source id="{self.chunk_id}">\n{header}\n{self.text.strip()}\n</source>'


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
    # Keyed case-insensitively. Models copy the *style* of the placeholder in
    # the instructions, so a prompt showing `[[cite:CHUNK_ID]]` reliably
    # produces `[[cite:CHK_01M25...]]` against an id of `chk_01M25...`. A
    # case-sensitive lookup scores a correctly-cited answer as 0% grounded and
    # reports every real citation as a hallucination.
    by_id = {item.chunk_id.casefold(): item for item in evidence}
    assigned: dict[str, int] = {}
    citations: list[Citation] = []
    unresolved: list[str] = []

    def number_for(item: EvidenceItem) -> int:
        """The reference number this source carries in the finished answer."""
        key = item.chunk_id
        if key not in assigned:
            assigned[key] = len(assigned) + 1
            citations.append(item.to_citation(assigned[key]))
        return assigned[key]

    def replace(match: re.Match[str]) -> str:
        ordinal = match.group("ordinal")
        if ordinal is not None:
            # A reference by the number shown in the source header.
            position = int(ordinal)
            if not 1 <= position <= len(evidence):
                # The model referred to a source it was never shown. Same
                # treatment as an invented id: removed and reported, because a
                # reference to nothing is worse than no reference.
                unresolved.append(match.group(0))
                return ""
            return f"[{number_for(evidence[position - 1])}]"

        numbers: list[int] = []
        for chunk_id in _marker_ids(match.group("ids")):
            item = by_id.get(chunk_id.casefold())
            if item is None:
                unresolved.append(chunk_id)
                continue
            numbers.append(number_for(item))
        return "".join(f"[{n}]" for n in numbers)

    resolved = MARKER_PATTERN.sub(replace, text)

    # Removing a marker can leave " ." or a double space behind.
    resolved = re.sub(r"\s+([.,;:!?])", r"\1", resolved)
    resolved = re.sub(r"[ \t]{2,}", " ", resolved).strip()

    return ResolvedAnswer(text=resolved, citations=citations, unresolved=unresolved)


def strip_markers(text: str) -> str:
    """Remove every citation marker, for plain-text contexts."""
    return re.sub(r"\s+([.,;:!?])", r"\1", CITE_PATTERN.sub("", text)).strip()
