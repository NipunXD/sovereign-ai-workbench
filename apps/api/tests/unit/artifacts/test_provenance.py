"""Provenance.

A generated report emailed onward looks exactly like one a person wrote. Nothing
travels with it except what is written inside, so this block is the only thing
standing between "the AI produced a number" and a reader being able to check it.
"""

from __future__ import annotations

from workbench.artifacts.provenance import Provenance
from workbench.ingest.ir import BBox
from workbench.rag.citations import Citation


def citation(n: int, doc_id: str, title: str, page: int, confidence: float = 1.0) -> Citation:
    return Citation(
        n=n,
        chunk_id=f"c{n}",
        doc_id=doc_id,
        doc_title=title,
        doc_type="inspection",
        page_no=page,
        bbox=BBox(),
        snippet="…",
        confidence=confidence,
    )


def test_sources_are_grouped_by_document_with_their_pages() -> None:
    """A reader wants "report X, pages 4 and 6", not three separate entries."""
    provenance = Provenance(
        citations=[
            citation(1, "d1", "Inspection Report 2029", 4),
            citation(2, "d1", "Inspection Report 2029", 6),
            citation(3, "d2", "SOP-4412", 11),
        ]
    )
    sources = {s["title"]: s for s in provenance.sources}
    assert sources["Inspection Report 2029"]["pages"] == [4, 6]
    assert sources["SOP-4412"]["pages"] == [11]


def test_a_poorly_scanned_source_is_flagged() -> None:
    """A figure read from a bad photocopy deserves a second look before it
    reaches a decision."""
    provenance = Provenance(
        citations=[
            citation(1, "d1", "Clean SOP", 1, confidence=1.0),
            citation(2, "d2", "Photocopied Report", 4, confidence=0.61),
        ]
    )
    assert provenance.has_uncertain_sources
    lines = "\n".join(provenance.source_lines())
    assert "61%" in lines
    assert "verify against the original" in lines
    # The clean source is not tarred with the same warning.
    assert "Clean SOP — page 1" in lines


def test_confident_sources_are_not_flagged() -> None:
    provenance = Provenance(citations=[citation(1, "d1", "Clean SOP", 1, confidence=0.97)])
    assert not provenance.has_uncertain_sources
    assert "verify" not in "\n".join(provenance.source_lines())


def test_the_lowest_confidence_for_a_document_wins() -> None:
    """One bad page makes the document worth checking, however good the rest."""
    provenance = Provenance(
        citations=[
            citation(1, "d1", "Mixed Report", 1, confidence=0.99),
            citation(2, "d1", "Mixed Report", 7, confidence=0.55),
        ]
    )
    assert provenance.sources[0]["lowest_confidence"] == 0.55
    assert provenance.has_uncertain_sources


def test_an_unapproved_document_says_so() -> None:
    """Silence would read as approval."""
    rows = dict(Provenance().lines())
    assert rows["Approved by"] == "not approved"


def test_an_approved_document_names_the_approver() -> None:
    rows = dict(Provenance(approved_by="M. Devadiga", approved_at="2029-04-02T10:00:00").lines())
    assert "M. Devadiga" in rows["Approved by"]


def test_no_sources_is_stated_rather_than_left_blank() -> None:
    """An empty reference list must not look like a document with sources that
    simply were not printed."""
    from workbench.artifacts.docx_builder import DocxBuilder, DocxSpec

    artifact = DocxBuilder().build(DocxSpec(title="Ungrounded"), Provenance())
    assert artifact.size_bytes > 0

    import io

    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(artifact.data)).paragraphs)
    assert "No documents were cited" in text


def test_the_digest_identifies_the_exact_file() -> None:
    provenance = Provenance(sha256="a" * 64)
    assert dict(provenance.lines())["Document digest"] == "a" * 64
