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

import re
from typing import Any, Literal

import pint
from pydantic import BaseModel, Field, field_validator

from workbench.tools.base import BaseTool, ToolContext, ToolResult, ToolSpec

#: One registry for the process; constructing one is expensive.
_units: Any = pint.UnitRegistry()

# Gauge pressure, which is how a refinery writes every pressure it has: the
# corpus says "design pressure 18.0 barg" and pint has never heard of barg, so
# the shell-thickness and MAWP calculations — the two an inspection engineer
# actually reaches for — failed on their most ordinary input.
#
# Defined as equal to the absolute unit rather than offset by an atmosphere.
# That is not a shortcut: the ASME and API formulas here take design pressure
# as a gauge quantity, so the magnitude is used as written, and converting to
# absolute would silently add 1 atm to every wall thickness.
_units.define("barg = bar")
_units.define("psig = psi")
_units.define("kPag = kPa")
_units.define("MPag = MPa")

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
        # Location is required. A corrosion rate is a property of one
        # measurement point, and a table holds a reading for that year at every
        # other point too — so without it the source check has to compare
        # against every CML on the vessel and a reading lifted from the wrong
        # one passes. Naming the point is also the cheapest way to make the
        # model commit to which row it is reading.
        "required": ["initial_thickness", "current_thickness", "interval", "location"],
        # The years stay optional because a report sometimes quotes a loss over
        # a period without dating either end, and refusing that would refuse
        # the source. When both are given the interval is computed from them
        # rather than believed, and the readings are checked against the
        # documents; when they are not, the result says so.
        "optional": ["initial_year", "current_year"],
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
            "from_year": "initial_year",
            "earlier_year": "initial_year",
            "initial_date": "initial_year",
            "to_year": "current_year",
            "later_year": "current_year",
            "final_year": "current_year",
            "current_date": "current_year",
            "cml": "location",
            "point": "location",
            "measurement_location": "location",
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
        "aliases": {
            "dt1": "delta_t1",
            "dt2": "delta_t2",
            "hot_end_approach": "delta_t1",
            "cold_end_approach": "delta_t2",
        },
    },
    "heat_duty": {
        "required": ["mass_flow", "specific_heat", "delta_t"],
        "aliases": {
            "flow": "mass_flow",
            "flow_rate": "mass_flow",
            "cp": "specific_heat",
            "temperature_rise": "delta_t",
            "dt": "delta_t",
        },
    },
    "orifice_flow": {
        "required": ["beta", "orifice_diameter", "differential_pressure", "density"],
        "optional": ["discharge_coefficient"],
        "aliases": {
            "beta_ratio": "beta",
            "diameter": "orifice_diameter",
            "dp": "differential_pressure",
            "delta_p": "differential_pressure",
            "fluid_density": "density",
            "cd": "discharge_coefficient",
        },
    },
}


#: "6 year(s)" — a plural the model writes for a human. pint reads the "(s)" as
#: multiplication by the second and hands back year·second, which is [time]**2
#: and fails dimensional analysis for reasons nobody can act on.
_PLURAL_PARENS = re.compile(r"\(\s*s\s*\)", re.IGNORECASE)

#: "3.30 mm/6 year" — the model writing a rate as the change over the interval
#: rather than per unit time. pint applies its own precedence and reads it as
#: (mm / 6) * year, so [length]*[time] instead of [length]/[time]. Division is
#: done here and the unit rewritten to the per-unit form the calculation wants.
_RATE_OVER_INTERVAL = re.compile(
    r"^\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<numerator>[A-Za-z°µ]+)\s*/\s*"
    r"(?P<divisor>\d+(?:\.\d+)?)\s*(?P<denominator>[A-Za-z°µ]+)\s*$"
)

#: "mm per year", "degC per hour" — written the way it is said aloud.
_SPELLED_PER = re.compile(r"\s+per\s+", re.IGNORECASE)


def _q(quantity: Any, places: int = 4) -> str:
    """A quantity written the way an engineer writes it.

    pint's compact format prints the magnitude as Python holds it, so
    subtracting 12.5 from 13.9 shows its working as "1.4000000000000004 mm".
    The arithmetic is right and the presentation destroys confidence in it,
    which on a page headed "check this yourself" is the whole cost.
    """
    magnitude = round(float(quantity.magnitude), places)
    return f"{magnitude:g} {quantity.units:~P}".strip()


def normalise_quantity(text: str) -> str:
    """Repair the unit strings a model writes but pint cannot read.

    These are not the model being wrong about the engineering — the number and
    the intent are right in every case here — they are the difference between
    how a person writes a quantity and how a parser reads one. Rejecting them
    produced a dimensional error that looked like a calculation failure, and
    the run went on to do the arithmetic in prose instead, which is exactly
    what this tool exists to prevent.
    """
    if not isinstance(text, str):
        return text
    cleaned = _PLURAL_PARENS.sub("", text).strip()
    cleaned = _SPELLED_PER.sub("/", cleaned)

    match = _RATE_OVER_INTERVAL.match(cleaned)
    if match:
        divisor = float(match.group("divisor"))
        if divisor:
            value = float(match.group("value")) / divisor
            # Trailing zeros removed so the working reads "0.55 mm/year"
            # rather than "0.5499999999999999 mm/year".
            return f"{round(value, 10):g} {match.group('numerator')}/{match.group('denominator')}"
    return cleaned


#: Inputs that are labels rather than measurements. They must not go through
#: the unit parser, which would reject "CML-04" as an undefined unit.
TEXT_INPUTS = frozenset({"location"})

#: A thickness plausible for a vessel wall, used to tell a reading apart from
#: the year sitting beside it in the same table row.
_PLAUSIBLE_MM = (0.1, 500.0)

#: A standalone number. The leading guard keeps the digits inside an
#: identifier out of it — CML-04 is a location, and reporting that the sources
#: record "04 mm" for a year is worse than saying nothing.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.\-/])\d+(?:\.\d+)?")


def readings_recorded_for(year: str, sources: list[str], location: str | None = None) -> list[str]:
    """Thicknesses the sources put beside a given year.

    The authoritative source here is a spreadsheet whose rows read
    ``V-1201 | CML-04 | 2023 | 12.5``, so a reading and its date sit on one
    line. Lines mentioning the year but carrying no plausible thickness — a
    report title, a commissioning date — yield nothing and leave the caller
    unable to conclude anything, which is the right outcome rather than a
    guess.
    """
    found: list[str] = []
    for source in sources:
        for line in source.splitlines():
            if year not in line:
                continue
            if location and location.lower() not in line.lower():
                continue
            for token in _NUMBER.findall(line):
                if token == year:
                    continue
                try:
                    value = float(token)
                except ValueError:
                    continue
                if _PLAUSIBLE_MM[0] <= value <= _PLAUSIBLE_MM[1] and token not in found:
                    found.append(token)
    return found


def contradicts_sources(
    year: str, thickness: Any, sources: list[str], location: str | None = None
) -> str | None:
    """Whether the sources record a different reading for that year.

    The check the dimensional one cannot make. Given self-consistent dates the
    calculator will happily divide two thicknesses that never belonged to those
    years — on a real run it was handed the 2019 thickness labelled 2023, with
    the 2029 reading beside it, and every number was real. Only the source says
    which reading belongs to which date.

    Returns a message when the sources clearly disagree, and None when they
    agree or cannot say. Silence on "cannot say" is deliberate: a scanned table
    whose columns came out of order says nothing about any year, and refusing
    on that basis would refuse the document.
    """
    recorded = readings_recorded_for(year, sources, location)
    if not recorded:
        return None
    claimed = round(float(thickness.to("mm").magnitude), 4)
    if any(abs(float(value) - claimed) < 0.005 for value in recorded):
        return None
    listed = ", ".join(f"{value} mm" for value in recorded[:6])
    where = f" at {location}" if location else ""
    return (
        f"the sources record {listed} for {year}{where}, not {claimed:g} mm. "
        f"Check that each thickness belongs to the year given for it."
    )


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
        cleaned.setdefault(canonical, normalise_quantity(value))
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
            "'15 bar'. For corrosion_rate, name the measurement location (the CML) "
            "and give initial_year and current_year: the interval is computed from "
            "the dates, and each thickness is checked against the reading the "
            "sources record for that year at that location. Take both thicknesses "
            "from the same pair of dates the question asks about. Required inputs "
            "per calculation — " + describe_parameters()
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
            return ToolResult.refuse(f"unsupported calculation '{args.calculation}'")

        cleaned, ignored = normalise_inputs(args.calculation, args.inputs)
        expected = PARAMETERS.get(args.calculation, {})
        missing = [name for name in expected.get("required", []) if name not in cleaned]
        if missing:
            return ToolResult.refuse(
                f"missing required input(s) for {args.calculation}: {', '.join(missing)}. "
                f"Expected: {', '.join(expected.get('required', []))}"
                + (
                    f" (optional: {', '.join(expected.get('optional', []))})"
                    if expected.get("optional")
                    else ""
                )
            )

        try:
            quantities: dict[str, Any] = {
                key: value if key in TEXT_INPUTS else _units.Quantity(value)
                for key, value in cleaned.items()
            }
        except (pint.UndefinedUnitError, pint.DimensionalityError, TypeError, ValueError) as exc:
            return ToolResult.refuse(f"could not parse the inputs: {exc}")

        # Checked against the documents before the arithmetic, not after: a
        # figure that was never in the sources should not reach a result the
        # report is then written from.
        if args.calculation == "corrosion_rate":
            sources = list((ctx.run_context or {}).get("source_text") or []) if ctx else []
            location = cleaned.get("location")
            for thickness_key, year_key in (
                ("initial_thickness", "initial_year"),
                ("current_thickness", "current_year"),
            ):
                year = cleaned.get(year_key)
                if not year or thickness_key not in quantities:
                    continue
                problem = contradicts_sources(
                    str(year).strip(), quantities[thickness_key], sources, location
                )
                if problem:
                    return ToolResult.refuse(problem)

        try:
            output = handler(quantities)
        except KeyError as exc:
            return ToolResult.refuse(f"missing required input: {exc}")
        except pint.DimensionalityError as exc:
            # The check that earns this tool its place: a wrong unit combination
            # fails loudly instead of producing a plausible wrong number.
            return ToolResult.refuse(f"dimensional error: {exc}")
        except ValueError as exc:
            # Raised by a handler when the inputs contradict each other — a
            # pair of readings whose dates do not match the interval given.
            # The message is written for the reader, so it is passed through
            # rather than prefixed with the exception class.
            return ToolResult.refuse(str(exc))
        except ZeroDivisionError:
            return ToolResult.refuse("division by zero — check the interval or rate inputs")
        except Exception as exc:
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

        if ignored:
            output.assumptions.append(f"Ignored unrecognised input(s): {', '.join(ignored)}.")
        return ToolResult(
            ok=True,
            data=output,
            metrics={"calculation": args.calculation, "ignored_inputs": ignored},
            # The working, for the panel. An engineer checking a wall thickness
            # needs the substitution and the standard, not a bare figure.
            display={
                "kind": "calculation",
                "calculation": output.calculation,
                "formatted": output.formatted,
                "value": output.value,
                "unit": output.unit,
                "standard_ref": output.standard_ref,
                "inputs": dict(cleaned),
                "steps": [step.model_dump() for step in output.steps],
                "assumptions": list(output.assumptions),
                "caveats": list(output.caveats),
            },
        )

    # ------------------------------------------------------------ mechanical
    @staticmethod
    def _corrosion_rate(q: dict[str, Any]) -> CalcOutput:
        """Uniform corrosion rate between two thickness measurements (API 510)."""
        initial = q["initial_thickness"].to("mm")
        current = q["current_thickness"].to("mm")
        interval = q["interval"].to("year")

        caveats: list[str] = []
        derived_from_years = False

        # The interval the model asserts is the weakest input here. Asked for
        # the 2023 and 2029 readings, a real run supplied the 2019 and 2023
        # thicknesses and an interval of six years — each value present in the
        # sources, the combination belonging to no pair of measurements that
        # exists. Nothing in three loose quantities ties a thickness to its
        # date, so when the dates are given the interval is computed from them.
        initial_year = q.get("initial_year")
        current_year = q.get("current_year")
        if initial_year is not None and current_year is not None:
            span = float(current_year.magnitude) - float(initial_year.magnitude)
            if span <= 0:
                raise ValueError(
                    f"the later reading is dated {current_year.magnitude:g} and the earlier one "
                    f"{initial_year.magnitude:g}. Check which reading is which."
                )
            stated = interval.magnitude
            if abs(span - stated) > 0.5:
                raise ValueError(
                    f"the readings are dated {initial_year.magnitude:g} and "
                    f"{current_year.magnitude:g}, which is {span:g} years apart, but the "
                    f"interval given is {stated:g} years. One of the two is wrong — check "
                    f"that the thicknesses belong to the years named."
                )
            interval = span * _units("year")
            derived_from_years = True

        loss = initial - current
        rate = (loss / interval).to("mm/year")

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

        if not derived_from_years:
            caveats.append(
                "The readings were not dated, so neither could be checked against the "
                "sources and the interval is the one supplied."
            )
        if derived_from_years:
            caveats.append(
                f"Interval taken from the dates given ({initial_year.magnitude:g} to "  # type: ignore[union-attr]
                f"{current_year.magnitude:g}), not from the interval supplied."  # type: ignore[union-attr]
            )

        return CalcOutput(
            calculation="corrosion_rate",
            value=float(rate.magnitude),
            unit="mm/year",
            formatted=f"{rate.magnitude:.4f} mm/year",
            steps=[
                CalcStep(
                    description="Metal loss over the interval",
                    expression=f"{_q(initial)} − {_q(current)}",
                    result=f"{_q(loss)}",
                ),
                CalcStep(
                    description="Divide by the elapsed interval",
                    expression=f"{_q(loss)} ÷ {_q(interval)}",
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
                    expression=f"{_q(current)} − {_q(minimum)}",
                    result=f"{_q(available)}",
                ),
                CalcStep(
                    description="Divide by the corrosion rate",
                    expression=f"{_q(available)} ÷ {_q(rate)}",
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
                    expression=f"({_q(pressure)} × {_q(radius)}) / ({_q(stress)} × {efficiency} − 0.6 × {_q(pressure)})",
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
                    expression=f"({_q(stress)} × {efficiency} × {_q(thickness)}) / ({_q(radius)} + 0.6 × {_q(thickness)})",
                    result=f"{mawp.magnitude:.3f} MPa",
                )
            ],
            standard_ref="ASME VIII Div.1 UG-27 rearranged for MAWP",
            assumptions=[
                "Thickness is the actual measured value less any future corrosion allowance."
            ],
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
                    expression=f"{_q(flow)} × {_q(cp)} × {_q(delta_t)}",
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
        discharge = (
            float(q["discharge_coefficient"].magnitude) if "discharge_coefficient" in q else 0.61
        )

        if not 0 < beta < 1:
            raise ValueError("beta ratio must be between 0 and 1")

        area = math.pi * (diameter.magnitude**2) / 4 * _units("m**2")
        velocity_factor = 1 / math.sqrt(1 - beta**4)
        flow = (discharge * velocity_factor * area * (2 * delta_p / density) ** 0.5).to("m**3/s")

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
