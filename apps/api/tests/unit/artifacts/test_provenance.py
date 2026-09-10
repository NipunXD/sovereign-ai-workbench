"""Provenance.

A generated report emailed onward looks exactly like one a person wrote. Nothing
travels with it except what is written inside, so this block is the only thing
standing between "the AI produced a number" and a reader being able to check it.
"""

from __future__ import annotations

import pytest

from workbench.artifacts.provenance import Provenance
from workbench.core.ir import BBox
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


class TestCitationRoundTrip:
    """A citation has to survive a trip through the database.

    An approval carries the run's provenance so a document can be produced
    after the run has gone, and JSONB stores dictionaries rather than
    dataclasses. The first version stored the objects directly and the insert
    failed with "Object of type Citation is not JSON serializable" — at the
    moment approval was requested, so the whole run died rather than the
    document.
    """

    def test_a_citation_survives_as_dict_and_back(self) -> None:
        from workbench.core.citation import Citation
        from workbench.core.ir import BBox

        original = Citation(
            n=2,
            chunk_id="chk_1",
            doc_id="doc_1",
            doc_title="Inspection Report V-1201",
            doc_type="inspection",
            page_no=3,
            bbox=BBox(x0=0.1, y0=0.2, x1=0.8, y1=0.4),
            snippet="CML-04 measured 9.20 mm",
            section_path=["2. Thickness Survey"],
            score=0.87,
            retrieval_method="hybrid",
            confidence=0.61,
        )
        restored = Citation.from_dict(original.as_dict())

        assert restored.doc_title == original.doc_title
        assert restored.page_no == original.page_no
        assert restored.snippet == original.snippet
        assert restored.section_path == original.section_path
        # The confidence is what marks a figure read off a bad photocopy, so
        # losing it in transit would quietly drop the warning from the page.
        assert restored.confidence == pytest.approx(0.61)
        assert restored.bbox.x0 == pytest.approx(0.1)
        assert restored.bbox.y1 == pytest.approx(0.4)

    def test_an_older_record_without_every_key_still_loads(self) -> None:
        from workbench.core.citation import Citation

        restored = Citation.from_dict({"n": 1, "chunk_id": "c", "doc_id": "d"})
        assert restored.doc_title == ""
        assert restored.confidence == 1.0

    def test_the_run_context_round_trips_for_storage(self) -> None:
        from workbench.agent.deferred import from_storable, to_storable
        from workbench.core.citation import Citation

        context = {
            "models": {"reasoning": "qwen/qwen3-8b"},
            "citations": [Citation(n=1, chunk_id="c1", doc_id="d1", doc_title="Report", page_no=1)],
            "tools": ["search_corpus"],
        }
        stored = to_storable(context)

        # Storable means exactly that: it has to go into a JSONB column.
        import json

        json.dumps(stored)

        restored = from_storable(stored)
        assert isinstance(restored["citations"][0], Citation)
        assert restored["citations"][0].doc_title == "Report"
        assert restored["models"] == context["models"]
