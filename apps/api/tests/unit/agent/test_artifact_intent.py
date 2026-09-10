"""Reading a request for a document out of what the user actually typed.

The detector exists because the planner's compliance is not a guarantee, and
its failure is silent — a run that answers in prose looks like a run that
worked. But a detector that over-fires is worse than the gap it closes:
generating a Word file because someone asked what a report *says* wastes a
minute of local inference and puts an unwanted document into the approval
queue. The negative cases below are the ones that matter.
"""

from __future__ import annotations

import pytest

from workbench.agent.artifact_intent import DOCX, PPTX, XLSX, requested_artifact

ALL_TOOLS = {DOCX, XLSX, PPTX, "calc.engineering"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The request that started this, verbatim.
        ("Produce a Word report on the V-1201 thickness survey findings.", DOCX),
        ("Generate a Word document summarising the V-1201 thickness survey.", DOCX),
        ("I need a report on P-101A vibration", DOCX),
        ("write this up as a memo", DOCX),
        ("draft a summary document for the turnaround", DOCX),
        ("Make me a briefing deck for the turnaround review", PPTX),
        ("Can you prepare a presentation on the 2029 inspection?", PPTX),
        ("build a slide deck from the CML readings", PPTX),
        ("Create a spreadsheet of the CML readings", XLSX),
        ("Export the thickness history to Excel", XLSX),
    ],
)
def test_a_request_to_produce_a_file_is_recognised(text: str, expected: str) -> None:
    assert requested_artifact(text, ALL_TOOLS) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Names a report, asks to be told about it.
        "What does the inspection report say about CML-04?",
        "Which report covers the 2023 survey?",
        "Show me the maintenance log",
        "Summarise the deck we retrieved earlier",
        "What is the depressurisation rate limit for V-1201?",
        "Has the vibration alert threshold for P-101A been exceeded?",
        "Find the 2029 inspection report",
        "",
    ],
)
def test_a_question_is_not_a_request_for_a_file(text: str) -> None:
    assert requested_artifact(text, ALL_TOOLS) is None


class TestFormatSelection:
    """The user's words choose the format. A deck must not become a report."""

    def test_the_most_specific_format_wins(self) -> None:
        # "Word report" contains "report"; it must not fall through to generic.
        assert requested_artifact("produce a Word report", ALL_TOOLS) == DOCX
        # "slide deck" contains neither "word" nor "excel".
        assert requested_artifact("produce a slide deck of the findings", ALL_TOOLS) == PPTX

    def test_a_deck_is_never_a_spreadsheet(self) -> None:
        assert requested_artifact("make a presentation of the CML table", ALL_TOOLS) == PPTX

    def test_a_named_object_is_required(self) -> None:
        """ "Write up the findings" is not a request for a file.

        It far more often means a written answer in the chat. Forcing a
        document there would be the over-firing this detector is supposed to
        avoid, so the planner's own judgement is left to stand.
        """
        assert requested_artifact("write up the findings", ALL_TOOLS) is None
        assert requested_artifact("write up the findings as a report", ALL_TOOLS) == DOCX


class TestPermission:
    """A format the caller cannot generate is not silently substituted."""

    def test_an_unavailable_tool_is_not_returned(self) -> None:
        assert requested_artifact("produce a slide deck", {DOCX}) is None

    def test_no_artifact_tools_at_all(self) -> None:
        assert requested_artifact("produce a Word report", {"calc.engineering"}) is None

    def test_an_available_format_is_still_found_when_others_are_not(self) -> None:
        assert requested_artifact("produce a Word report", {DOCX}) == DOCX


class TestDistance:
    def test_a_verb_far_from_the_object_does_not_count(self) -> None:
        # The verb and the noun are in unrelated clauses.
        text = (
            "Generate the corrosion rate for CML-04 and tell me whether the vessel "
            "stays in service, then explain what the retirement criteria mean in "
            "practice for the next turnaround window and the inspection report"
        )
        assert requested_artifact(text, ALL_TOOLS) is None

    def test_case_and_spacing_do_not_matter(self) -> None:
        assert requested_artifact("PRODUCE   A   WORD\n REPORT", ALL_TOOLS) == DOCX
