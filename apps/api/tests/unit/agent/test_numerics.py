"""Verifying that the figures in an answer appear in the cited documents.

The failure this exists to catch: a plausible number with a real citation
attached to it. Every other check passes that answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from workbench.agent import numerics
from workbench.agent.numerics import extract, normalise, verify

SOURCE = (
    "INSP-2029-V1201 Inspection Report V-1201. 1. Equipment Details. "
    "Design pressure 18.0 barg. Design temperature 180 degC. Material SA-516 "
    "Gr.70. Nominal shell thickness 14.0 mm. 2. Thickness Survey. CML-04 "
    "bottom shell measured 9.20 mm against a t-min of 8.00 mm."
)


class TestExtraction:
    def test_a_measurement_is_a_number_with_a_unit(self) -> None:
        figures = extract("The limit is 2 bar per minute and the hold is 4 hours.")
        assert [f.text for f in figures] == ["2 bar", "4 hours"]

    def test_a_decimal_counts_even_without_a_unit(self) -> None:
        assert [f.value for f in extract("The ratio fell to 0.55 this year.")] == ["0.55"]

    def test_bare_small_integers_are_not_measurements(self) -> None:
        """Otherwise every step list and count flags a well-grounded answer."""
        assert extract("There are 3 steps and both of the 2 valves are closed.") == []

    def test_equipment_tags_are_names_not_numbers(self) -> None:
        """V-1201 is a vessel. Demanding "1201" appear as a figure is nonsense."""
        assert extract("Vessel V-1201 per SOP-4412 at CML-04 and PSV-1207.") == []

    def test_citation_markers_are_references_not_measurements(self) -> None:
        assert extract("The rate is capped [1]. See also [2][3].") == []

    def test_a_table_alignment_row_is_not_data(self) -> None:
        figures = extract("| CML | mm |\n|---|---:|\n| CML-04 | 9.20 |")
        assert [f.value for f in figures] == ["9.2"]

    def test_the_same_figure_twice_is_reported_once(self) -> None:
        assert len(extract("18.0 barg, and again 18.0 barg.")) == 1


class TestNormalisation:
    @pytest.mark.parametrize(
        ("written", "expected"),
        [("18.0", "18"), ("9.20", "9.2"), ("1,200", "1200"), ("8.00", "8"), ("0.550", "0.55")],
    )
    def test_trailing_zeros_and_separators_do_not_change_a_value(
        self, written: str, expected: str
    ) -> None:
        assert normalise(written) == expected


class TestVerification:
    def test_a_figure_quoted_from_the_source_is_found(self) -> None:
        figures = verify("The design pressure is 18.0 barg.", {1: SOURCE})
        assert [(f.text, f.found, f.sources) for f in figures] == [("18.0 barg", True, [1])]

    def test_trailing_zeros_do_not_defeat_the_match(self) -> None:
        """The source says 9.20 mm; an answer saying 9.2 mm is the same reading."""
        assert verify("CML-04 is at 9.2 mm.", {1: SOURCE})[0].found

    def test_an_invented_figure_is_not_found(self) -> None:
        """The case this module exists for: a plausible number, a real citation."""
        figures = verify("The design pressure is 24.5 barg.", {1: SOURCE})
        assert figures[0].found is False
        assert figures[0].sources == []

    def test_it_reports_which_source_carries_the_figure(self) -> None:
        figures = verify("Thickness is 14.0 mm.", {1: "unrelated text", 2: SOURCE})
        assert figures[0].sources == [2]

    def test_an_answer_with_no_measurements_reports_nothing(self) -> None:
        assert verify("The documents do not cover that.", {1: SOURCE}) == []

    def test_an_answer_with_no_sources_finds_nothing(self) -> None:
        figures = verify("The design pressure is 18.0 barg.", {})
        assert figures[0].found is False

    def test_empty_answer_is_not_an_error(self) -> None:
        assert verify("", {1: SOURCE}) == []


#: One successful corrosion-rate call, as the runner records it.
RESULT: dict[str, Any] = {
    "ok": True,
    "display": {
        "kind": "calculation",
        "calculation": "corrosion_rate",
        "value": 0.55,
        "steps": [
            {"description": "Metal lost", "expression": "12.5 mm − 9.2 mm", "result": "3.3 mm"}
        ],
        "inputs": {"initial_thickness": "12.5 mm", "current_thickness": "9.2 mm"},
    },
}


class TestCalculatedFigures:
    """A number the run derived is not a number the run failed to source."""

    def test_a_derived_figure_is_marked_computed(self) -> None:
        figures = numerics.verify(
            "The rate is 0.55 mm/year [1].",
            {1: "CML-04 measured 9.2 mm in 2029."},
            numerics.calculated([RESULT]),
        )
        rate = next(f for f in figures if f.value == "0.55")
        assert rate.found is False
        assert rate.computed == "corrosion_rate"
        assert rate.accounted_for is True

    def test_a_step_result_counts_too(self) -> None:
        figures = numerics.verify("Metal lost: 3.3 mm.", {}, numerics.calculated([RESULT]))
        assert figures[0].computed == "corrosion_rate"

    def test_the_source_wins_when_both_have_it(self) -> None:
        """A document is better provenance than our own arithmetic."""
        figures = numerics.verify(
            "The 2029 reading is 9.2 mm [1].",
            {1: "CML-04 measured 9.2 mm in 2029."},
            numerics.calculated([RESULT]),
        )
        assert figures[0].found is True
        assert figures[0].computed == ""

    def test_an_input_is_not_laundered_by_being_passed_to_a_tool(self) -> None:
        """Crediting inputs would let an invented reading clear itself by
        being handed to the calculator."""
        figures = numerics.verify(
            "The 2023 reading was 12.5 mm.", {}, numerics.calculated([RESULT])
        )
        assert figures[0].accounted_for is False

    def test_a_refused_calculation_produced_nothing(self) -> None:
        refused = {"ok": False, "refused": True, "display": None}
        assert numerics.calculated([refused]) == {}

    def test_an_unrelated_figure_is_still_unaccounted_for(self) -> None:
        figures = numerics.verify(
            "Design pressure is 18.0 barg.", {}, numerics.calculated([RESULT])
        )
        assert figures[0].accounted_for is False
