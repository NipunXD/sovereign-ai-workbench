"""Tests for refusal detection.

The cases marked "observed" are verbatim answers the running system produced
against the seed corpus. They are here because the previous fixed-phrase
detector classified all three as non-refusals, which reported the grounding
eval's refusal rate as 0.20 when it was in fact 1.00.
"""

from __future__ import annotations

import pytest

from workbench.agent.refusal import is_refusal

OBSERVED_REFUSALS = [
    pytest.param(
        "The indexed documents do not specify the purge duration used during "
        "the 2019 turnaround. The relevant SOP-4412 procedure outlines nitrogen "
        "purge criteria (e.g., hydrocarbon content <1% LEL, three volume "
        "changes) [1], but no duration is explicitly stated.",
        id="observed-do-not-specify",
    ),
    pytest.param(
        "The indexed documents do not cover vessel V-9999. The provided sources "
        "only reference vessel V-1201, which has a design pressure of 18.0 barg "
        "[1]. No data exists for V-9999 in the provided materials.",
        id="observed-do-not-cover",
    ),
    pytest.param(
        "The current plant manager of MRPL is not explicitly mentioned in the "
        'provided documents. The closest relevant role noted is "General Manager '
        '(Maintenance)" in internal memos [1], but this title does not directly '
        'equate to "plant manager."',
        id="observed-not-explicitly-mentioned",
    ),
]

PHRASINGS = [
    "The documents do not contain information about this.",
    "The retrieved passages don't mention that equipment.",
    "This is not recorded in any of the provided sources.",
    "The corpus lacks any reference to that tag.",
    "The provided material fails to address the 2019 turnaround.",
    "I could not find any record of a 2031 inspection.",
    "There is no record of nitrogen consumption for the last purge.",
    "This question cannot be answered from the retrieved passages.",
    "No information on the 2019 turnaround is available in the indexed corpus.",
    "I am unable to determine the design pressure from these excerpts.",
    "There is insufficient evidence in the sources to answer this.",
]

ANSWERS = [
    "The depressurisation rate limit for V-1201 is 2 bar per minute [1].",
    "The minimum required shell thickness for V-1201 is 8.0 mm [1].",
    "Hold at 4 barg for four hours at 120 degC [1]. The vessel must not be "
    "vented faster than 2 bar/min.",
    "The vibration alert threshold is 4.5 mm/s RMS and the shutdown threshold is 7.1 mm/s RMS [2].",
]


@pytest.mark.parametrize("text", OBSERVED_REFUSALS)
def test_answers_the_system_actually_produced_are_refusals(text: str) -> None:
    assert is_refusal(text)


@pytest.mark.parametrize("text", PHRASINGS)
def test_recognises_refusals_across_phrasings(text: str) -> None:
    assert is_refusal(text)


@pytest.mark.parametrize("text", ANSWERS)
def test_a_real_answer_is_not_a_refusal(text: str) -> None:
    assert not is_refusal(text)


class TestTheDangerousDirection:
    """A real answer misread as a refusal is exempted from grounding checks.

    That is the error worth guarding, so it gets its own cases.
    """

    def test_a_caveat_before_a_real_answer_is_not_a_refusal(self) -> None:
        assert not is_refusal(
            "The 2023 report does not contain this figure, but the 2029 survey "
            "shows the minimum reading at CML-04 is 9.20 mm [2], below the "
            "8.0 mm retirement thickness."
        )

    def test_however_carries_the_same_weight_as_but(self) -> None:
        assert not is_refusal(
            "The SOP does not state a tolerance, however the measured value is 9.20 mm [1]."
        )

    def test_a_negated_contrast_clause_stays_a_refusal(self) -> None:
        # The contrast guard must not swallow genuine refusals that hedge.
        assert is_refusal(
            "The documents do not specify a purge duration, but they do not rule one out either."
        )

    def test_a_cited_answer_followed_by_a_caveat_is_not_a_refusal(self) -> None:
        # Produced live: a correct, cited figure, then a sentence shaped exactly
        # like a refusal. The footer read "declined — not in the corpus".
        assert not is_refusal(
            "The design pressure of V-1201 is **18.0 barg**.\n\n"
            "| Parameter | Value | Source |\n|---|---|---|\n"
            "| Design Pressure | 18.0 barg | [1] |\n\n"
            "This value is explicitly stated in the inspection report "
            '(INSP-2029-V1201) under "Equipment Details" [1]. Other sources '
            "describe operational procedures but do not specify design pressure."
        )

    def test_a_table_does_not_stretch_the_window(self) -> None:
        # Rows have no full stops; before, three of them and the sentence after
        # counted as one sentence and dragged a fourth-sentence caveat inside.
        assert not is_refusal(
            "The rate limit is 2 bar per minute [1].\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n"
            "Second point [1]. Third point [2]. The sources do not state a tolerance."
        )

    def test_a_restatement_before_a_refusal_still_refuses(self) -> None:
        # A restatement cites nothing, so it does not count as an answer.
        assert is_refusal(
            "You asked for the purge duration used in the 2019 turnaround. "
            "The indexed documents do not specify a purge duration."
        )

    def test_a_late_caveat_does_not_retroactively_refuse(self) -> None:
        assert not is_refusal(
            "The depressurisation rate limit is 2 bar per minute [1]. The hold "
            "time is four hours at 120 degC [1]. Vent valves are listed in the "
            "P&ID [3]. The sources do not state an ambient temperature limit."
        )


class TestEdges:
    def test_empty_is_not_a_refusal(self) -> None:
        # A crashed synthesis step must not score as an honest abstention.
        assert not is_refusal("")
        assert not is_refusal("   \n  ")

    def test_case_insensitive(self) -> None:
        assert is_refusal("THE DOCUMENTS DO NOT CONTAIN THIS INFORMATION.")

    def test_window_is_configurable(self) -> None:
        text = "First. Second. Third. The documents do not contain this."
        assert not is_refusal(text, sentences=3)
        assert is_refusal(text, sentences=4)
