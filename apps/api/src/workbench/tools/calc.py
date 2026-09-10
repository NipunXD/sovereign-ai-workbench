"""Engineering calculations with dimensional checking.

A closed catalogue, not a general expression evaluator. That is the point: an
LLM asked to compute a corrosion rate will produce a confident number with no
way to tell whether it divided by the right interval. Here the arithmetic is
ordinary Python, the model only chooses the calculation and supplies inputs, and
``pint`` rejects a dimensionally invalid combination outright.

Every result carries its working and the standard it follows, because an
inspection engineer has to be able to check it — and because "the AI said 0.31
mm/yr" is not something anyone should act on.
"""

from __future__ import annotations

from typing import Any, Literal

import pint
from pydantic import BaseModel, Field, field_validator

from workbench.tools.base import BaseTool, ToolContext, ToolResult, ToolSpec

#: One registry for the process; constructing one is expensive.
_units = pint.UnitRegistry()

Calculation = Literal[
    "corrosion_rate",
    "remaining_life",
    "minimum_thickness_shell",
    "mawp_shell",
    "orifice_flow",
    "lmtd",
    "heat_duty",
]


#: Expected inputs per calculation, and the aliases models reach for instead.
#:
#: A free-form dict gives a model nothing to aim at, so it invents plausible
#: names — "final_thickness" for "current_thickness", "time_difference" for
#: "interval". Rejecting those would be technically correct and practically
#: useless, so known aliases are accepted and anything genuinely unrecognised
#: produces an error naming the parameters that were expected.
PARAMETERS: dict[str, dict[str, Any]] = {
    "corrosion_rate": {
        "required": ["initial_thickness", "current_thickness", "interval"],
        "aliases": {
            "final_thickness": "current_thickness",
            "later_thickness": "current_thickness",
            "previous_thickness": "initial_thickness",
            "earlier_thickness": "initial_thickness",
            "original_thickness": "initial_thickness",
            "time_difference": "interval",
            "elapsed_time": "interval",
            "period": "interval",
            "years": "interval",
        },
    },
    "remaining_life": {
        "required": ["current_thickness", "minimum_thickness", "corrosion_rate"],
        "aliases": {
            "t_min": "minimum_thickness",
            "minimum_required_thickness": "minimum_thickness",
            "retirement_thickness": "minimum_thickness",
            "actual_thickness": "current_thickness",
            "rate": "corrosion_rate",
        },
    },
    "minimum_thickness_shell": {
        "required": ["design_pressure", "inside_radius", "allowable_stress"],
        "optional": ["joint_efficiency"],
        "aliases": {
            "pressure": "design_pressure",
            "radius": "inside_radius",
            "stress": "allowable_stress",
            "efficiency": "joint_efficiency",
            "weld_efficiency": "joint_efficiency",
        },
    },
    "mawp_shell": {
        "required": ["thickness", "inside_radius", "allowable_stress"],
        "optional": ["joint_efficiency"],
        "aliases": {
            "actual_thickness": "thickness",
            "radius": "inside_radius",
            "stress": "allowable_stress",
            "efficiency": "joint_efficiency",
        },
    },
    "lmtd": {
        "required": ["delta_t1", "delta_t2"],
        "aliases": {"dt1": "delta_t1", "dt2": "delta_t2",
                    "hot_end_approach": "delta_t1", "cold_end_approach": "delta_t2"},
    },
    "heat_duty": {
        "required": ["mass_flow", "specific_heat", "delta_t"],
        "aliases": {"flow": "mass_flow", "flow_rate": "mass_flow",
                    "cp": "specific_heat", "temperature_rise": "delta_t", "dt": "delta_t"},
    },
    "orifice_flow": {
        "required": ["beta", "orifice_diameter", "differential_pressure", "density"],
        "optional": ["discharge_coefficient"],
        "aliases": {"beta_ratio": "beta", "diameter": "orifice_diameter",
                    "dp": "differential_pressure", "delta_p": "differential_pressure",
                    "fluid_density": "density", "cd": "discharge_coefficient"},
    },
}


def normalise_inputs(calculation: str, raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Map aliases onto canonical names and drop anything unrecognised.

    Returns the cleaned inputs and the names that were discarded, so the caller
    can say what it ignored rather than failing on a stray key.
    """
    spec = PARAMETERS.get(calculation, {})
    known = set(spec.get("required", [])) | set(spec.get("optional", []))
    aliases: dict[str, str] = spec.get("aliases", {})

    cleaned: dict[str, str] = {}
    ignored: list[str] = []
    for key, value in raw.items():
        canonical = key if key in known else aliases.get(key.lower().strip())
        if canonical is None:
            ignored.append(key)
            continue
        cleaned.setdefault(canonical, value)
    return cleaned, ignored


def describe_parameters() -> str:
    """Render the expected inputs into the tool description.

    Without this the binder is guessing at key names, which is exactly how the
    wrong ones get invented.
    """
    lines = []
    for name, spec in PARAMETERS.items():
        required = ", ".join(spec.get("required", []))
        optional = spec.get("optional") or []
        suffix = f" (optional: {', '.join(optional)})" if optional else ""
        lines.append(f"{name}: {required}{suffix}")
    return "; ".join(lines)


class CalcInput(BaseModel):
    """Inputs for one calculation.

    Values are given with explicit units. A bare number is where unit errors
    come from, and unit errors in a refinery are not a rounding problem.
    """

    calculation: Calculation = Field(description="Which calculation to perform")
    inputs: dict[str, str] = Field(
        description=(
            "Named quantities with units, e.g. "
            '{"initial_thickness": "12.5 mm", "current_thickness": "9.2 mm", '
            '"interval": "6 year"}'
        )
    )

    @field_validator("inputs")
    @classmethod
    def _non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("at least one input quantity is required")
        return v


class CalcStep(BaseModel):
    description: str
    expression: str = ""
    result: str = ""


class CalcOutput(BaseModel):
    calculation: str
    value: float
    unit: str
    formatted: str
    steps: list[CalcStep] = Field(default_factory=list)
    standard_ref: str = ""
    assumptions: list[str] = Field(default_factory=list)
    #: Set when a result should not be acted on without review — for instance a
    #: remaining life computed from only two thickness readings.
    caveats: list[str] = Field(default_factory=list)


class EngineeringCalcTool(BaseTool):
    """The closed catalogue of supported calculations."""

    spec = ToolSpec(
        name="calc.engineering",
        description=(
            "Perform a standard refinery engineering calculation with dimensional "
            "checking. Every quantity must carry its unit, e.g. '9.2 mm', '6 year', "
            "'15 bar'. Required inputs per calculation — "
            + describe_parameters()
        ),
        input_model=CalcInput,
        output_model=CalcOutput,
        required_permissions=frozenset({"chat:use"}),
        side_effect="none",
        requires_approval=False,
        timeout_s=10,
    )

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, CalcInput)
        handler = getattr(self, f"_{args.calculation}", None)
        if handler is None:
            return ToolResult.failure(f"unsupported calculation '{args.calculation}'")

        cleaned, ignored = normalise_inputs(args.calculation, args.inputs)
        expected = PARAMETERS.get(args.calculation, {})
        missing = [name for name in expected.get("required", []) if name not in cleaned]
        if missing:
            return ToolResult.failure(
                f"missing required input(s) for {args.calculation}: {', '.join(missing)}. "
                f"Expected: {', '.join(expected.get('required', []))}"
                + (f" (optional: {', '.join(expected.get('optional', []))})"
                   if expected.get("optional") else "")
            )

        try:
            quantities = {key: _units.Quantity(value) for key, value in cleaned.items()}
        except (pint.UndefinedUnitError, pint.DimensionalityError, TypeError, ValueError) as exc:
            return ToolResult.failure(f"could not parse the inputs: {exc}")

        try:
            output = handler(quantities)
        except KeyError as exc:
            return ToolResult.failure(f"missing required input: {exc}")
        except pint.DimensionalityError as exc:
            # The check that earns this tool its place: a wrong unit combination
            # fails loudly instead of producing a plausible wrong number.
            return ToolResult.failure(f"dimensional error: {exc}")
        except ZeroDivisionError:
            return ToolResult.failure("division by zero — check the interval or rate inputs")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

        if ignored:
            output.assumptions.append(f"Ignored unrecognised input(s): {', '.join(ignored)}.")
        return ToolResult(
            ok=True,
            data=output,
            metrics={"calculation": args.calculation, "ignored_inputs": ignored},
        )

    # ------------------------------------------------------------ mechanical
    @staticmethod
    def _corrosion_rate(q: dict[str, Any]) -> CalcOutput:
        """Uniform corrosion rate between two thickness measurements (API 510)."""
        initial = q["initial_thickness"].to("mm")
        current = q["current_thickness"].to("mm")
        interval = q["interval"].to("year")

        loss = initial - current
        rate = (loss / interval).to("mm/year")

        caveats: list[str] = []
        if loss.magnitude < 0:
            caveats.append(
                "The current reading is thicker than the initial one. This usually "
                "means a different CML was measured, or the readings were transposed."
            )
        if interval.magnitude < 1:
            caveats.append(
                "The interval is under a year, so the extrapolated annual rate is "
                "sensitive to measurement error."
            )

        return CalcOutput(
            calculation="corrosion_rate",
            value=float(rate.magnitude),
            unit="mm/year",
            formatted=f"{rate.magnitude:.4f} mm/year",
            steps=[
                CalcStep(
                    description="Metal loss over the interval",
                    expression=f"{initial:~P} − {current:~P}",
                    result=f"{loss:~P}",
                ),
                CalcStep(
                    description="Divide by the elapsed interval",
                    expression=f"{loss:~P} ÷ {interval:~P}",
                    result=f"{rate.magnitude:.4f} mm/year",
                ),
            ],
            standard_ref="API 510 / API 570 — uniform corrosion rate",
            assumptions=["Corrosion is uniform between the two measurements."],
            caveats=caveats,
        )

    @staticmethod
    def _remaining_life(q: dict[str, Any]) -> CalcOutput:
        """Remaining life against a minimum required thickness (API 510)."""
        current = q["current_thickness"].to("mm")
        minimum = q["minimum_thickness"].to("mm")
        rate = q["corrosion_rate"].to("mm/year")

        available = current - minimum
        caveats: list[str] = []

        if rate.magnitude <= 0:
            return CalcOutput(
                calculation="remaining_life",
                value=float("inf"),
                unit="year",
                formatted="no measurable corrosion — remaining life is not limited by wall loss",
                standard_ref="API 510 §7.1",
                caveats=["The corrosion rate is zero or negative; verify the readings."],
            )

        life = (available / rate).to("year")
        if available.magnitude <= 0:
            caveats.append(
                "The current thickness is already at or below the minimum required. "
                "This is a fitness-for-service condition requiring immediate review."
            )

        return CalcOutput(
            calculation="remaining_life",
            value=float(life.magnitude),
            unit="year",
            formatted=f"{life.magnitude:.2f} years",
            steps=[
                CalcStep(
                    description="Thickness available for corrosion",
                    expression=f"{current:~P} − {minimum:~P}",
                    result=f"{available:~P}",
                ),
                CalcStep(
                    description="Divide by the corrosion rate",
                    expression=f"{available:~P} ÷ {rate:~P}",
                    result=f"{life.magnitude:.2f} year",
                ),
            ],
            standard_ref="API 510 §7.1 — remaining life",
            assumptions=["The measured corrosion rate continues unchanged."],
            caveats=caveats,
        )

    @staticmethod
    def _minimum_thickness_shell(q: dict[str, Any]) -> CalcOutput:
        """Required shell thickness for internal pressure (ASME VIII-1 UG-27)."""
        pressure = q["design_pressure"].to("MPa")
        radius = q["inside_radius"].to("mm")
        stress = q["allowable_stress"].to("MPa")
        efficiency = float(q["joint_efficiency"].magnitude) if "joint_efficiency" in q else 1.0

        denominator = stress * efficiency - 0.6 * pressure
        if denominator.magnitude <= 0:
            raise ValueError(
                "design pressure is too high for this material and joint efficiency "
                "(S·E − 0.6·P is not positive); the circumferential-stress formula "
                "does not apply"
            )
        thickness = (pressure * radius / denominator).to("mm")

        return CalcOutput(
            calculation="minimum_thickness_shell",
            value=float(thickness.magnitude),
            unit="mm",
            formatted=f"{thickness.magnitude:.3f} mm",
            steps=[
                CalcStep(
                    description="t = P·R / (S·E − 0.6·P)",
                    expression=f"({pressure:~P} × {radius:~P}) / ({stress:~P} × {efficiency} − 0.6 × {pressure:~P})",
                    result=f"{thickness.magnitude:.3f} mm",
                )
            ],
            standard_ref="ASME VIII Div.1 UG-27(c)(1) — circumferential stress",
            assumptions=[
                "Cylindrical shell under internal pressure.",
                "Excludes corrosion allowance and mill tolerance.",
            ],
        )

    @staticmethod
    def _mawp_shell(q: dict[str, Any]) -> CalcOutput:
        """Maximum allowable working pressure from actual thickness."""
        thickness = q["thickness"].to("mm")
        radius = q["inside_radius"].to("mm")
        stress = q["allowable_stress"].to("MPa")
        efficiency = float(q["joint_efficiency"].magnitude) if "joint_efficiency" in q else 1.0

        mawp = ((stress * efficiency * thickness) / (radius + 0.6 * thickness)).to("MPa")
        return CalcOutput(
            calculation="mawp_shell",
            value=float(mawp.magnitude),
            unit="MPa",
            formatted=f"{mawp.magnitude:.3f} MPa ({mawp.to('bar').magnitude:.2f} bar)",
            steps=[
                CalcStep(
                    description="P = S·E·t / (R + 0.6·t)",
                    expression=f"({stress:~P} × {efficiency} × {thickness:~P}) / ({radius:~P} + 0.6 × {thickness:~P})",
                    result=f"{mawp.magnitude:.3f} MPa",
                )
            ],
            standard_ref="ASME VIII Div.1 UG-27 rearranged for MAWP",
            assumptions=["Thickness is the actual measured value less any future corrosion allowance."],
        )

    # ------------------------------------------------------------ process
    @staticmethod
    def _lmtd(q: dict[str, Any]) -> CalcOutput:
        """Log mean temperature difference for a heat exchanger."""
        dt1 = q["delta_t1"].to("kelvin")
        dt2 = q["delta_t2"].to("kelvin")
        a, b = float(dt1.magnitude), float(dt2.magnitude)
        if a <= 0 or b <= 0:
            raise ValueError("both terminal temperature differences must be positive")

        import math

        # The formula is indeterminate when the two differences are equal, and
        # numerically unstable as they approach each other.
        value = a if abs(a - b) < 1e-6 else (a - b) / math.log(a / b)
        return CalcOutput(
            calculation="lmtd",
            value=value,
            unit="K",
            formatted=f"{value:.2f} K",
            steps=[
                CalcStep(
                    description="LMTD = (ΔT₁ − ΔT₂) / ln(ΔT₁/ΔT₂)",
                    expression=f"({a:.2f} − {b:.2f}) / ln({a:.2f}/{b:.2f})",
                    result=f"{value:.2f} K",
                )
            ],
            standard_ref="Counter-current log mean temperature difference",
            assumptions=["Counter-current flow; constant specific heats."],
        )

    @staticmethod
    def _heat_duty(q: dict[str, Any]) -> CalcOutput:
        """Sensible heat duty: Q = m·cp·ΔT."""
        flow = q["mass_flow"].to("kg/s")
        cp = q["specific_heat"].to("kJ/(kg*K)")
        delta_t = q["delta_t"].to("kelvin")

        duty = (flow * cp * delta_t).to("kW")
        return CalcOutput(
            calculation="heat_duty",
            value=float(duty.magnitude),
            unit="kW",
            formatted=f"{duty.magnitude:.2f} kW",
            steps=[
                CalcStep(
                    description="Q = ṁ · cp · ΔT",
                    expression=f"{flow:~P} × {cp:~P} × {delta_t:~P}",
                    result=f"{duty.magnitude:.2f} kW",
                )
            ],
            standard_ref="Sensible heat balance",
            assumptions=["No phase change; constant specific heat over the range."],
        )

    @staticmethod
    def _orifice_flow(q: dict[str, Any]) -> CalcOutput:
        """Incompressible flow through an orifice plate (ISO 5167)."""
        import math

        beta = float(q["beta"].magnitude)
        diameter = q["orifice_diameter"].to("m")
        delta_p = q["differential_pressure"].to("Pa")
        density = q["density"].to("kg/m**3")
        discharge = float(q["discharge_coefficient"].magnitude) if "discharge_coefficient" in q else 0.61

        if not 0 < beta < 1:
            raise ValueError("beta ratio must be between 0 and 1")

        area = math.pi * (diameter.magnitude**2) / 4 * _units("m**2")
        velocity_factor = 1 / math.sqrt(1 - beta**4)
        flow = (
            discharge * velocity_factor * area * (2 * delta_p / density) ** 0.5
        ).to("m**3/s")

        return CalcOutput(
            calculation="orifice_flow",
            value=float(flow.magnitude),
            unit="m^3/s",
            formatted=f"{flow.magnitude:.5f} m³/s ({flow.to('m**3/hour').magnitude:.2f} m³/h)",
            steps=[
                CalcStep(
                    description="Q = C·E·A·√(2ΔP/ρ)",
                    expression=f"C={discharge}, E={velocity_factor:.4f}, A={area.magnitude:.6f} m²",
                    result=f"{flow.magnitude:.5f} m³/s",
                )
            ],
            standard_ref="ISO 5167 — orifice plate, incompressible flow",
            assumptions=[
                "Incompressible fluid; no expansibility correction applied.",
                f"Discharge coefficient assumed {discharge}.",
            ],
        )
