"""Normalising model-authored text before it reaches a document.

Both cases here came out of a real generated report. Nothing crashed; the
document simply came out wrong in ways a reader notices immediately and a test
suite does not.
"""

from __future__ import annotations

from workbench.tools.artifacts import (
    BriefingSlide,
    DocxInput,
    PptxInput,
    ReportSection,
    _body_blocks,
    _lines,
    _safe_heading,
    _to_docx_spec,
    _to_pptx_spec,
)

#: Verbatim from a generated V-1201 report: the model wrote the newline as two
#: characters inside a JSON string, and the document rendered it that way.
OBSERVED = (
    "Key findings include:\\n- CML-04 on the bottom shell showed the highest "
    "loss.\\n- Corrosion rate 0.55 mm/yr."
)


class TestLines:
    def test_an_escaped_newline_becomes_a_real_break(self) -> None:
        assert _lines(OBSERVED) == [
            "Key findings include:",
            "- CML-04 on the bottom shell showed the highest loss.",
            "- Corrosion rate 0.55 mm/yr.",
        ]

    def test_real_newlines_work_too(self) -> None:
        assert _lines("one\ntwo\n\nthree") == ["one", "two", "three"]

    def test_plain_text_is_unchanged(self) -> None:
        assert _lines("The limit is 2 bar per minute.") == ["The limit is 2 bar per minute."]

    def test_no_backslash_n_survives_into_a_document(self) -> None:
        assert all("\\n" not in line for line in _lines(OBSERVED))


class TestBodyBlocks:
    def test_list_lines_become_bullets(self) -> None:
        blocks = _body_blocks(OBSERVED)
        assert [b.type for b in blocks] == ["paragraph", "bullets"]
        assert blocks[1].items == [
            "CML-04 on the bottom shell showed the highest loss.",
            "Corrosion rate 0.55 mm/yr.",
        ]

    def test_numbered_lines_become_bullets_without_their_numbers(self) -> None:
        blocks = _body_blocks("Recommendations:\\n1. Monitor CML-04.\\n2. Re-survey in 12 months.")
        assert blocks[1].items == ["Monitor CML-04.", "Re-survey in 12 months."]

    def test_prose_stays_one_paragraph(self) -> None:
        blocks = _body_blocks("The survey was performed to API 510.")
        assert [b.type for b in blocks] == ["paragraph"]


class TestReservedHeadings:
    """The provenance page is the one part of the document the model cannot write.

    A model-authored section titled "Provenance" appeared above the real one in
    a generated report, listing sources it had decided on itself. Two sections
    with the same heading leaves a reader unable to tell which is authoritative
    — and the whole value of the real page is that it is not the model's word.
    """

    def test_a_reserved_heading_is_marked_as_the_model_s_own(self) -> None:
        assert _safe_heading("Provenance") == "Provenance (as stated in the answer)"
        assert _safe_heading("Sources:") == "Sources (as stated in the answer)"

    def test_an_ordinary_heading_is_untouched(self) -> None:
        assert _safe_heading("Survey Results") == "Survey Results"

    def test_the_content_is_kept_not_dropped(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="Report",
                sections=[
                    ReportSection(heading="Provenance", body="Something worth keeping.", sources=[])
                ],
            )
        )
        text = " ".join(b.text for b in spec.blocks)
        assert "Something worth keeping." in text
        assert "Provenance (as stated in the answer)" in text


class TestEndToEndMapping:
    def test_a_report_body_carries_no_escape_artefacts(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="V-1201 Survey",
                sections=[ReportSection(heading="Survey Results", body=OBSERVED, sources=[])],
            )
        )
        for block in spec.blocks:
            assert "\\n" not in block.text
            assert all("\\n" not in item for item in block.items)

    def test_a_deck_splits_a_two_line_bullet(self) -> None:
        spec = _to_pptx_spec(
            PptxInput(
                title="Briefing",
                slides=[BriefingSlide(heading="Findings", bullets=["first\\nsecond", "third"])],
            )
        )
        assert spec.slides[0].bullets == ["first", "second", "third"]


class TestTruncatedJsonDebris:
    """A truncated tool-argument object leaves JSON structure in the prose.

    Observed in a generated report as a source line ending
    `... 7. Retirement Criteria."}],`.
    """

    def test_the_observed_fragment_is_removed(self) -> None:
        line = 'SOP-4412 Vessel Shutdown Procedure — page 1 — 7. Retirement Criteria."}],'
        assert _lines(line) == [
            "SOP-4412 Vessel Shutdown Procedure — page 1 — 7. Retirement Criteria."
        ]

    def test_legitimate_brackets_survive(self) -> None:
        """The first version of this rule keyed on brackets alone and ate them.

        Corrupting correct text is a worse fault than leaving the artefact, so
        these are the cases that decide the rule's shape.
        """
        assert _lines("The reading was taken at CML-04 (see figure 2)") == [
            "The reading was taken at CML-04 (see figure 2)"
        ]
        assert _lines("Values: [12.50, 9.20]") == ["Values: [12.50, 9.20]"]
        assert _lines("Hold for four hours (per SOP-4412).") == [
            "Hold for four hours (per SOP-4412)."
        ]

    def test_a_closing_quote_alone_is_not_debris(self) -> None:
        assert _lines('He said "monitor it"') == ['He said "monitor it"']

    def test_ordinary_prose_is_untouched(self) -> None:
        assert _lines("The limit is 2 bar per minute.") == ["The limit is 2 bar per minute."]


class TestNumberedReservedHeadings:
    """A model writing a structured report numbers its sections.

    So the clash with the builder's own provenance page arrives as "7.
    Provenance", not "Provenance", and an exact match let it straight through
    — a model-authored provenance section sitting directly above the real one,
    which is the confusion the guard exists to prevent.
    """

    def test_a_numbered_reserved_heading_is_caught(self) -> None:
        assert _safe_heading("7. Provenance") == "7. Provenance (as stated in the answer)"
        assert _safe_heading("2) Sources") == "2) Sources (as stated in the answer)"

    def test_an_ordinary_numbered_heading_is_untouched(self) -> None:
        assert _safe_heading("3. Thickness Measurements") == "3. Thickness Measurements"
        assert _safe_heading("1. Introduction") == "1. Introduction"


class TestSectionSources:
    """A section that names its sources but carries no inline markers.

    Asked to thread [n] markers through prose while filling a JSON schema
    under constrained decoding, the model produced none at all on a live run
    — the references page was numbered and the body pointed at nothing. A
    list of source numbers per section is something it fills reliably, and
    it is enough to attribute every section that states a figure.
    """

    def test_sources_are_attached_to_the_end_of_the_prose(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="R",
                sections=[
                    ReportSection(heading="Findings", body="CML-04 is at 9.20 mm.", sources=[2, 1])
                ],
            )
        )
        paragraph = next(b for b in spec.blocks if b.type == "paragraph")
        assert paragraph.text == "CML-04 is at 9.20 mm. [1][2]"

    def test_a_table_gets_its_sources_as_a_caption(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="R",
                sections=[
                    ReportSection(
                        heading="Readings",
                        table_headers=["CML", "mm"],
                        table_rows=[["CML-04", "9.20"]],
                        sources=[3],
                    )
                ],
            )
        )
        table = next(b for b in spec.blocks if b.type == "table")
        assert table.caption == "Sources: [3]"

    def test_inline_markers_take_precedence(self) -> None:
        # The model did the better thing; the trail must not double up.
        spec = _to_docx_spec(
            DocxInput(
                title="R",
                sections=[
                    ReportSection(
                        heading="F", body="9.20 mm [2] against 8.0 mm [1].", sources=[1, 2]
                    )
                ],
            )
        )
        paragraph = next(b for b in spec.blocks if b.type == "paragraph")
        assert paragraph.text == "9.20 mm [2] against 8.0 mm [1]."

    def test_bullets_carry_the_trail_when_there_is_no_table(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="R",
                sections=[ReportSection(heading="F", bullets=["one", "two"], sources=[4])],
            )
        )
        bullets = next(b for b in spec.blocks if b.type == "bullets")
        assert bullets.items == ["one", "two [4]"]

    def test_no_sources_means_no_trail(self) -> None:
        spec = _to_docx_spec(
            DocxInput(
                title="R", sections=[ReportSection(heading="F", body="Unattributed.", sources=[])]
            )
        )
        paragraph = next(b for b in spec.blocks if b.type == "paragraph")
        assert paragraph.text == "Unattributed."
