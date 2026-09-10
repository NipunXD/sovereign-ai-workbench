"""Engineering calculations.

The reason this tool exists rather than letting the model do arithmetic: an LLM
produces a confident number with no way to tell whether it divided by the right
interval. These tests pin the arithmetic and, more importantly, prove that a
dimensionally invalid combination fails loudly instead of returning a plausible
wrong answer.
"""

from __future__ import annotations

import math

import pytest

from workbench.security.rbac import Principal
from workbench.tools.base import ToolContext
from workbench.tools.calc import EngineeringCalcTool


@pytest.fixture
def tool() -> EngineeringCalcTool:
    return EngineeringCalcTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        principal=Principal(user_id="u", username="eng", permissions=frozenset({"chat:use"}))
    )


async def run(tool: EngineeringCalcTool, ctx: ToolContext, calculation: str, **inputs: str):
    return await tool.run(
        tool.parse_args({"calculation": calculation, "inputs": inputs}), ctx
    )


# --- arithmetic --------------------------------------------------------------
async def test_corrosion_rate(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="12.5 mm", current_thickness="9.2 mm", interval="6 year",
    )
    assert result.ok
    assert result.data.value == pytest.approx(0.55, abs=1e-6)  # (12.5-9.2)/6
    assert result.data.unit == "mm/year"
    assert "API 510" in result.data.standard_ref


async def test_remaining_life(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(
        tool, ctx, "remaining_life",
        current_thickness="9.2 mm", minimum_thickness="8 mm", corrosion_rate="0.55 mm/year",
    )
    assert result.ok
    assert result.data.value == pytest.approx(1.2 / 0.55, rel=1e-6)


async def test_minimum_shell_thickness(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    """ASME VIII-1 UG-27: t = P·R / (S·E − 0.6·P)."""
    result = await run(
        tool, ctx, "minimum_thickness_shell",
        design_pressure="15 bar", inside_radius="1200 mm",
        allowable_stress="138 MPa", joint_efficiency="0.85",
    )
    assert result.ok
    expected = (1.5 * 1200) / (138 * 0.85 - 0.6 * 1.5)
    assert result.data.value == pytest.approx(expected, rel=1e-6)


async def test_lmtd(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(tool, ctx, "lmtd", delta_t1="60 K", delta_t2="25 K")
    assert result.ok
    assert result.data.value == pytest.approx((60 - 25) / math.log(60 / 25), rel=1e-6)


async def test_lmtd_equal_deltas_does_not_divide_by_zero(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    """The formula is indeterminate when the terminal differences are equal."""
    result = await run(tool, ctx, "lmtd", delta_t1="30 K", delta_t2="30 K")
    assert result.ok
    assert result.data.value == pytest.approx(30.0)


async def test_heat_duty(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(
        tool, ctx, "heat_duty", mass_flow="12 kg/s", specific_heat="2.1 kJ/(kg*K)", delta_t="45 K"
    )
    assert result.ok
    assert result.data.value == pytest.approx(12 * 2.1 * 45, rel=1e-6)


async def test_mawp_round_trips_with_minimum_thickness(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    """MAWP at the minimum required thickness must return the design pressure."""
    thickness = await run(
        tool, ctx, "minimum_thickness_shell",
        design_pressure="15 bar", inside_radius="1200 mm",
        allowable_stress="138 MPa", joint_efficiency="0.85",
    )
    mawp = await run(
        tool, ctx, "mawp_shell",
        thickness=f"{thickness.data.value} mm", inside_radius="1200 mm",
        allowable_stress="138 MPa", joint_efficiency="0.85",
    )
    assert mawp.ok
    assert mawp.data.value == pytest.approx(1.5, rel=1e-4)  # 15 bar in MPa


# --- unit safety -------------------------------------------------------------
async def test_wrong_dimension_is_rejected(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    """A length where a time belongs must fail, not produce a number.

    This is the whole reason the calculation is not left to the model.
    """
    result = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="12.5 mm", current_thickness="9.2 mm", interval="6 mm",
    )
    assert not result.ok
    assert "dimensional" in result.error.lower()


async def test_undefined_unit_is_rejected(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(tool, ctx, "lmtd", delta_t1="60 blorks", delta_t2="25 K")
    assert not result.ok
    assert "blorks" in result.error


async def test_missing_input_names_the_field(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    result = await run(tool, ctx, "remaining_life", current_thickness="9.2 mm")
    assert not result.ok
    assert "minimum_thickness" in result.error


async def test_units_are_converted_not_assumed(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    """Inches and months must give the same answer as mm and years."""
    metric = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="12.5 mm", current_thickness="9.2 mm", interval="6 year",
    )
    imperial = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="0.4921259842519685 inch",
        current_thickness="0.36220472440944884 inch",
        interval="72 month",
    )
    assert imperial.ok
    assert imperial.data.value == pytest.approx(metric.data.value, rel=1e-4)


# --- honesty about results ---------------------------------------------------
async def test_transposed_readings_are_flagged(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    """Arithmetically valid, physically suspect — say so rather than report it flat."""
    result = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="9.2 mm", current_thickness="12.5 mm", interval="6 year",
    )
    assert result.ok
    assert result.data.value < 0
    assert any("thicker" in c for c in result.data.caveats)


async def test_thickness_below_minimum_is_flagged(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    """A vessel already under t-min is a fitness-for-service condition."""
    result = await run(
        tool, ctx, "remaining_life",
        current_thickness="7.5 mm", minimum_thickness="8 mm", corrosion_rate="0.55 mm/year",
    )
    assert result.ok
    assert any("fitness-for-service" in c for c in result.data.caveats)


async def test_zero_corrosion_does_not_divide_by_zero(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    result = await run(
        tool, ctx, "remaining_life",
        current_thickness="9.2 mm", minimum_thickness="8 mm", corrosion_rate="0 mm/year",
    )
    assert result.ok
    assert result.data.value == float("inf")
    assert result.data.caveats


async def test_impossible_pressure_is_refused(tool: EngineeringCalcTool, ctx: ToolContext) -> None:
    """When S·E − 0.6·P is not positive the formula does not apply."""
    result = await run(
        tool, ctx, "minimum_thickness_shell",
        design_pressure="500 MPa", inside_radius="1200 mm", allowable_stress="138 MPa",
    )
    assert not result.ok


async def test_every_result_carries_working_and_a_standard(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    """An inspection engineer has to be able to check the number."""
    result = await run(
        tool, ctx, "corrosion_rate",
        initial_thickness="12.5 mm", current_thickness="9.2 mm", interval="6 year",
    )
    assert result.data.steps
    assert all(step.description for step in result.data.steps)
    assert result.data.standard_ref
    assert result.data.assumptions


async def test_unsupported_calculation_is_refused(
    tool: EngineeringCalcTool, ctx: ToolContext
) -> None:
    """The catalogue is closed; this is not a general expression evaluator."""
    from workbench.core.errors import ToolError

    with pytest.raises(ToolError):
        tool.parse_args({"calculation": "solve_navier_stokes", "inputs": {"x": "1 m"}})
