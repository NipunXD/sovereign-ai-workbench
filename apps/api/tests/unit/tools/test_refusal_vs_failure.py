"""A tool that declines its inputs is not a tool that broke.

Drawn the same way, the two read identically — a red line saying the
calculator failed. An evaluator watching the trace concludes the calculator is
broken, when what actually happened is the check catching a reading the
documents place in a different year. That is the control working, and it is
the reason a calculation goes through a tool rather than a sentence.
"""

from __future__ import annotations

import pytest

from workbench.tools.base import ToolContext, ToolResult
from workbench.tools.calc import CalcInput, EngineeringCalcTool

pytestmark = pytest.mark.anyio

TABLE = "Tag | CML | Year | Thickness_mm\nV-1201 | CML-04 | 2023 | 12.5"


def _ctx():
    return ToolContext(principal=None, run_context={"source_text": [TABLE]})  # type: ignore[arg-type]


class TestTheTwoAreDistinguishable:
    def test_a_failure_is_not_marked_as_a_refusal(self) -> None:
        result = ToolResult.failure("the backend was unreachable")
        assert result.ok is False
        assert result.refused is False
        assert "refused" not in result.summary()

    def test_a_refusal_says_so_on_the_event(self) -> None:
        result = ToolResult.refuse("the units do not agree")
        assert result.ok is False
        assert result.refused is True
        assert result.summary()["refused"] is True


class TestCalcRefusesRatherThanFails:
    @pytest.mark.parametrize(
        ("label", "inputs"),
        [
            (
                "a reading the documents date differently",
                {
                    "initial_thickness": "13.90 mm",
                    "initial_year": "2023",
                    "current_thickness": "9.20 mm",
                    "current_year": "2029",
                    "interval": "6 year",
                    "location": "CML-04",
                },
            ),
            (
                "dates that contradict the interval",
                {
                    "initial_thickness": "12.50 mm",
                    "initial_year": "2019",
                    "current_thickness": "9.20 mm",
                    "current_year": "2029",
                    "interval": "6 year",
                    "location": "CML-04",
                },
            ),
            (
                "a missing required input",
                {"initial_thickness": "12.50 mm", "current_thickness": "9.20 mm"},
            ),
            (
                "a pressure where a rate belongs",
                {
                    "current_thickness": "9.2 mm",
                    "minimum_thickness": "8.0 mm",
                    "corrosion_rate": "18 barg",
                },
            ),
        ],
    )
    async def test_a_bad_input_is_a_refusal(self, label: str, inputs: dict[str, str]) -> None:
        calculation = "remaining_life" if "corrosion_rate" in inputs else "corrosion_rate"
        result = await EngineeringCalcTool().run(
            CalcInput(calculation=calculation, inputs=inputs), _ctx()
        )
        assert result.ok is False, label
        assert result.refused is True, f"{label} was reported as a crash, not a refusal"
