"""How much of an answer carries a citation.

This number drives the retrieve-loop and the badge a reader sees, so a false
alarm is expensive: it teaches people to ignore the badge, and the badge is the
whole point of a grounded system.
"""

from __future__ import annotations

import pytest

from workbench.agent.runner import AgentRunner

coverage = AgentRunner._coverage


def test_marker_in_its_own_trailing_block_counts() -> None:
    """The form models actually produce.

    Asked to cite, a model routinely ends the passage and puts the marker on a
    line of its own. Requiring the marker inside a sentence reported this — a
    correctly cited answer — as completely ungrounded.
    """
    ratio, uncited = coverage(
        "The depressurisation rate limit is 2 bar per minute and the hold is four hours.\n\n[1]"
    )
    assert ratio == 1.0
    assert not uncited


def test_inline_markers_count() -> None:
    ratio, _ = coverage("The limit is 2 bar per minute [1]. The hold is four hours [2].")
    assert ratio == 1.0


def test_uncited_paragraph_is_penalised() -> None:
    """The case the metric exists to catch: a claim with nothing behind it."""
    ratio, uncited = coverage(
        "The limit is 2 bar per minute [1].\n\n"
        "The vessel was replaced in 2019 and has not been inspected since."
    )
    assert ratio == pytest.approx(0.5)
    assert len(uncited) == 1
    assert "2019" in uncited[0]


def test_no_citations_scores_zero() -> None:
    ratio, uncited = coverage("The limit is 2 bar per minute and the hold time is four hours.")
    assert ratio == 0.0
    assert uncited


def test_headings_do_not_count_as_uncited_claims() -> None:
    """A markdown heading is structure, not an assertion needing a source."""
    ratio, _ = coverage("### Calculations\n\nThe corrosion rate is 0.55 mm/year [1].")
    assert ratio == 1.0


def test_short_fragments_are_ignored() -> None:
    ratio, _ = coverage("Yes [1].\n\nOK.")
    assert ratio == 1.0
