"""The unit strings a model writes, and whether the calculator can read them.

Every case here was produced by the real agent on the real corpus and made the
tool fail dimensional analysis. None of them is the model being wrong about the
engineering — the numbers and the intent are right in all of them. They are the
gap between how a person writes a quantity and how a parser reads one, and the
cost of that gap is not a visible error: the run carries on and does the
arithmetic in prose instead, which is the one thing this tool exists to stop.
"""

from __future__ import annotations

import pytest

from workbench.tools.calc import CalcInput, EngineeringCalcTool, normalise_quantity

pytestmark = pytest.mark.anyio


class TestNormalisation:
    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            # pint reads the "(s)" as multiplication by the second, so this
            # arrived as year·second — [time] squared.
            ("6 year(s)", "6 year"),
            ("18 month(s)", "18 month"),
            # A rate written as the change over the interval. pint's precedence
            # makes "mm/6 year" mean (mm / 6) × year, so [length]·[time].
            ("3.30 mm/6 year", "0.55 mm/year"),
            ("1.4 mm/2.5 year", "0.56 mm/year"),
            # Said aloud rather than written.
            ("0.55 mm per year", "0.55 mm/year"),
        ],
    )
    def test_a_repairable_quantity_is_repaired(self, written: str, expected: str) -> None:
        assert normalise_quantity(written) == expected

    @pytest.mark.parametrize(
        "written",
        ["12.50 mm", "0.55 mm/year", "18.0 barg", "138 MPa", "6 year", "1200 mm"],
    )
    def test_a_well_formed_quantity_is_left_alone(self, written: str) -> None:
        assert normalise_quantity(written) == written


class TestCalculationsThatUsedToFail:
    async def test_corrosion_rate_with_a_plural_interval(self) -> None:
        tool = EngineeringCalcTool()
        result = await tool.run(
            CalcInput(
                calculation="corrosion_rate",
                inputs={
                    "initial_thickness": "13.90 mm",
                    "current_thickness": "12.50 mm",
                    "interval": "6 year(s)",
                },
            ),
            None,
        )
        assert result.ok, result.error
        assert result.data.unit == "mm/year"
        assert result.data.value == pytest.approx(1.4 / 6, rel=1e-3)

    async def test_remaining_life_with_a_rate_written_over_its_interval(self) -> None:
        tool = EngineeringCalcTool()
        result = await tool.run(
            CalcInput(
                calculation="remaining_life",
                inputs={
                    "current_thickness": "9.2 mm",
                    "minimum_thickness": "8.0 mm",
                    "corrosion_rate": "3.30 mm/6 year",
                },
            ),
            None,
        )
        assert result.ok, result.error
        assert result.data.value == pytest.approx(1.2 / 0.55, rel=1e-3)

    async def test_shell_thickness_from_a_gauge_pressure(self) -> None:
        """Every pressure in a refinery is written in barg, which pint does not know."""
        tool = EngineeringCalcTool()
        result = await tool.run(
            CalcInput(
                calculation="minimum_thickness_shell",
                inputs={
                    "design_pressure": "18.0 barg",
                    "inside_radius": "1200 mm",
                    "allowable_stress": "138 MPa",
                },
            ),
            None,
        )
        assert result.ok, result.error
        assert result.data.unit == "mm"


class TestWorkingIsLegible:
    async def test_the_steps_do_not_print_floating_point_noise(self) -> None:
        """13.9 − 12.5 is 1.4, and on a page headed "check this yourself" it
        must not read "1.4000000000000004"."""
        tool = EngineeringCalcTool()
        result = await tool.run(
            CalcInput(
                calculation="corrosion_rate",
                inputs={
                    "initial_thickness": "13.90 mm",
                    "current_thickness": "12.50 mm",
                    "interval": "6 year",
                },
            ),
            None,
        )
        assert result.ok, result.error
        rendered = " ".join(f"{s.expression} {s.result}" for s in result.data.steps)
        assert "0000000" not in rendered and "9999999" not in rendered
        assert "1.4 mm" in rendered


class TestDimensionalCheckingStillBites:
    async def test_a_genuinely_wrong_unit_is_still_refused(self) -> None:
        """The repairs must not turn the tool into one that accepts anything —
        the dimensional check is the reason it exists."""
        tool = EngineeringCalcTool()
        result = await tool.run(
            CalcInput(
                calculation="remaining_life",
                inputs={
                    "current_thickness": "9.2 mm",
                    "minimum_thickness": "8.0 mm",
                    # A pressure where a corrosion rate belongs.
                    "corrosion_rate": "18 barg",
                },
            ),
            None,
        )
        assert result.ok is False
        assert "dimensional" in (result.error or "").lower()
