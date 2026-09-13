"""Restoring the spaces OCR drops.

The recogniser reads this corpus almost perfectly and then loses the gaps
between words: on the seed scans the character error ignoring spaces is 1.5%
while the word error was 44.7%. Every rule under test only inserts a space, so
the failure mode to guard against is not a wrong character — it is a space
inserted where the document did not have one, which quietly breaks an
equipment tag or a decimal.
"""

from __future__ import annotations

import pytest

from workbench.ingest.ocr.engine import split_run_together


class TestSpacesRestored:
    @pytest.mark.parametrize(
        ("recognised", "expected"),
        [
            # The dominant case: a full stop welded to the next word.
            ("Drum.Design pressure", "Drum. Design pressure"),
            ("barg.Material SA-516", "barg. Material SA-516"),
            ("2019.All locations", "2019. All locations"),
            # A tag welded to its label breaks a search for the tag.
            ("Equipment:V-1201", "Equipment: V-1201"),
            # A comma against a word is always a lost space.
            ("12.50mm,down from", "12.50 mm, down from"),
            # A unit against its reading.
            ("18.0barg at 120degC", "18.0 barg at 120 degC"),
            # And the case that was already handled, still handled.
            ("CrudeFeedSurgeDrum", "Crude Feed Surge Drum"),
        ],
    )
    def test_a_lost_space_is_put_back(self, recognised: str, expected: str) -> None:
        assert split_run_together(recognised) == expected


class TestNothingElseIsTouched:
    """The expensive failure. A tag that has been "corrected" is worse than one
    that was never touched, because it is wrong and looks deliberate."""

    @pytest.mark.parametrize(
        "text",
        [
            "SOP-4412 CML-04 V-1201 PSV-1207",
            # A stop before a digit: the grade and the reading must survive.
            "Material SA-516 Gr.70",
            "Design pressure 18.0 barg",
            # Initials are not words. "U.S.A." must not become "U. S. A.".
            "U.S.A. standard applies",
            "Issued by: S. Nair, Senior Inspection Engineer",
            # A stop before a lowercase letter is a filename or an address.
            "see report.pdf for detail",
            "a.pinto@mrpl.co.in",
            "https://workbench.local/docs",
            # A colon between digits is a time.
            "gas test at 07:20",
            # Ordinals are not a number welded to a unit.
            "the 1st and 2nd pass",
            # Already correct text must come back unchanged.
            "The depressurisation rate limit is 2 bar per minute.",
        ],
    )
    def test_text_that_was_already_right_is_unchanged(self, text: str) -> None:
        assert split_run_together(text) == text
