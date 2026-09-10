"""Deciding whether the user asked for a file.

The planner is told to plan an artifact tool step when a document is
requested, and mostly does. Mostly is not a guarantee, and the failure is
silent in the worst way: the run answers in prose, the person who asked for a
report gets an explanation of one, and because no tool ran the approval gate
never fires — a document tool that never fires is indistinguishable from a
document tool that is correctly permitted.

So the request is also read here, and a plan missing the step it needs is
repaired. Two rules keep that from firing when it should not:

*A verb and an object are both required.* "What does the inspection report say
about CML-04?" names a report and asks for nothing to be produced; generating a
Word file in reply would be absurd. Only a request to *make* something counts.

*The user's own words decide the format.* Asking for a deck must not produce a
spreadsheet. A named object is required too: "produce a report" is a request,
"write up the findings" is not — it far more often means a written answer in
the chat than a file to open, and guessing wrong there costs a minute of local
inference and puts an unwanted document in the approval queue. When the words
are that ambiguous the planner's own judgement is left to stand.
"""

from __future__ import annotations

import re

#: Tool names, matched against what the principal is actually offered.
DOCX = "artifact.docx"
XLSX = "artifact.xlsx"
PPTX = "artifact.pptx"

#: Asking for something to be *made*. "Show me", "find", "what does X say" are
#: deliberately absent: they ask to be told, not handed a file.
_PRODUCE = (
    r"(?:produce|generate|create|make|build|write|draft|prepare|compile|assemble|"
    r"put\s+together|export|give\s+me|send\s+me|i\s+(?:want|need)|"
    r"can\s+you\s+(?:make|write|produce|create|prepare))"
)

#: Format words, most specific first — "slide deck" must not be read as a doc.
_FORMATS: tuple[tuple[str, str], ...] = (
    (PPTX, r"(?:power\s?point|pptx?|slide\s?deck|slides?|deck|presentation|briefing\s+deck)"),
    (XLSX, r"(?:excel|xlsx?|spread\s?sheet|work\s?book|csv|table\s+of\s+figures)"),
    (
        DOCX,
        r"(?:word\s+(?:doc\w*|report|file)?|docx?|document|report|memo|write-?up|summary\s+doc\w*)",
    ),
)

#: A bare "report"/"summary" with a producing verb but no format word.
_GENERIC = r"(?:report|write-?up|document|summary|minutes|note)"

#: How far apart the verb and the object may sit and still be one request.
_SPAN = 60


#: What each tool produces, for saying so in plain words.
FORMAT_NAMES: dict[str, str] = {
    DOCX: "Word document",
    XLSX: "Excel workbook",
    PPTX: "PowerPoint deck",
}


def detect_format(text: str) -> str | None:
    """The artifact tool this request asks for, ignoring who is asking.

    Separate from :func:`requested_artifact` because the two questions have
    different answers and both matter. "Which file did they ask for?" is a
    fact about the sentence; "can this person produce it?" is a fact about
    their role. Collapsing them would leave the run unable to distinguish a
    request it should ignore from one it must decline out loud.
    """
    if not text:
        return None
    lowered = " ".join(text.lower().split())

    for tool, pattern in _FORMATS:
        # The verb must come before the object and stay close to it, so
        # "summarise the deck we retrieved" is not read as a request to build
        # one.
        if re.search(rf"\b{_PRODUCE}\b.{{0,{_SPAN}}}?\b{pattern}\b", lowered):
            return tool

    if re.search(rf"\b{_PRODUCE}\b.{{0,{_SPAN}}}?\b{_GENERIC}\b", lowered):
        return DOCX
    return None


def requested_artifact(text: str, available: set[str]) -> str | None:
    """The artifact tool this request asks for *and* the caller may use.

    Args:
        text: The user's message.
        available: Tool names this principal may actually use. A format the
            caller cannot generate returns None rather than a tool that would
            be dropped later — the run explains the limitation instead of
            silently planning something impossible.
    """
    wanted = detect_format(text)
    return wanted if wanted is not None and wanted in available else None
