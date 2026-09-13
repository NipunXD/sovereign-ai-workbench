"""When the answer does not use the number the calculator produced.

Synthesis is the terminal step, so a generated document is assembled from tool
results *before* the answer exists. That is fine while the two agree, and stops
being fine the moment the model re-reads its sources while writing and decides
the tool was working from the wrong inputs — which is a real run, reproduced
below. The screen said 0.55 mm/year, the signed document said 0.2333, and
nothing in the run mentioned the two disagreed.
"""

from __future__ import annotations

from workbench.agent.runner import AgentRunner


def _state(tool_results: list[dict]) -> dict:
    return {"tool_results": tool_results}


def _calc(value: float, formatted: str, *, name: str = "corrosion_rate", artifacts=()) -> dict:
    return {
        "tool": "calc.engineering",
        "ok": True,
        "artifacts": list(artifacts),
        "display": {
            "kind": "calculation",
            "calculation": name,
            "value": value,
            "formatted": formatted,
        },
    }


class TestDisagreement:
    def test_an_answer_that_ignores_the_calculation_is_flagged(self) -> None:
        state = _state([_calc(0.2333333, "0.2333 mm/year")])
        messages = AgentRunner._calculation_overridden(
            state,  # type: ignore[arg-type]
            "The corrosion rate is 0.55 mm/year and the remaining life is 2.18 years.",
        )
        assert len(messages) == 1
        assert "0.2333 mm/year" in messages[0]

    def test_the_document_is_named_when_one_was_produced(self) -> None:
        """The point of the notice: the file that was signed is the stale one."""
        state = _state(
            [
                _calc(0.2333333, "0.2333 mm/year"),
                {"tool": "artifact.docx", "ok": True, "artifacts": ["report.docx"]},
            ]
        )
        messages = AgentRunner._calculation_overridden(
            state,  # type: ignore[arg-type]
            "The corrosion rate is 0.55 mm/year.",
        )
        assert "document" in messages[0]

    def test_two_ignored_calculations_are_both_reported(self) -> None:
        state = _state(
            [_calc(0.2333333, "0.2333 mm/year"), _calc(5.14, "5.14 years", name="remaining_life")]
        )
        messages = AgentRunner._calculation_overridden(
            state,  # type: ignore[arg-type]
            "The rate is 0.55 mm/year and the remaining life is 2.18 years.",
        )
        assert len(messages) == 2


class TestAgreement:
    def test_an_answer_that_uses_the_result_is_not_flagged(self) -> None:
        state = _state([_calc(0.2333333, "0.2333 mm/year")])
        assert (
            AgentRunner._calculation_overridden(
                state,  # type: ignore[arg-type]
                "The corrosion rate is 0.2333 mm/year.",
            )
            == []
        )

    def test_a_rounded_figure_still_counts_as_used(self) -> None:
        """Flagging an answer that wrote 0.23 for 0.2333 would teach the reader
        to ignore the notice, which costs more than the notice is worth."""
        state = _state([_calc(0.2333333, "0.2333 mm/year")])
        assert (
            AgentRunner._calculation_overridden(
                state,  # type: ignore[arg-type]
                "The corrosion rate is about 0.23 mm/year.",
            )
            == []
        )

    def test_a_failed_calculation_is_not_a_disagreement(self) -> None:
        """It produced no number, so there is nothing for the answer to ignore."""
        state = _state([{"tool": "calc.engineering", "ok": False, "display": None}])
        assert AgentRunner._calculation_overridden(state, "Some answer.") == []  # type: ignore[arg-type]

    def test_a_run_with_no_calculations_says_nothing(self) -> None:
        assert AgentRunner._calculation_overridden(_state([]), "Some answer.") == []  # type: ignore[arg-type]
