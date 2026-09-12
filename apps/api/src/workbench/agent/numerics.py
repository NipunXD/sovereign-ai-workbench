"""Checking that the figures in an answer came from the documents.

The grounding ratio answers "does this paragraph carry a citation" and the
resolver answers "does that citation point at a passage we actually
retrieved". Neither answers the question an inspector actually has, which is
*where did that number come from*. A model can write a plausible figure and
attach a real citation to it, and every check we had would pass.

So this one is deliberately literal: take every measurement in the answer and
look for the same number in the passages the answer cites. No embeddings, no
second model, no judgement — a string of digits is either in the source text
or it is not. That makes it fast enough to run on every answer and, more
importantly, makes its verdict something a person can check by eye in the
source panel.

Two decisions keep the false-alarm rate down, which matters because a badge
nobody trusts is worse than no badge:

  - Only measurements are checked. A bare small integer is usually a step
    number, a count, or part of a sentence ("both of the 2 valves"), and
    demanding it appear in a source would flag every well-grounded answer. A
    number qualifies if it carries a recognised engineering unit or if it is
    written as a decimal, which is how readings are written.
  - Equipment tags are not numbers. V-1201, CML-04 and SOP-4412 are names that
    happen to contain digits, and the digit run inside them is skipped.

A figure that is not found is not called a hallucination. It may have been
computed — a corrosion rate derived from two readings appears in no document
by construction. The report says what it knows: found, or not found, and in
which source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Units a refinery writes. Matched case-insensitively, longest first so that
#: "mm/year" wins over "mm". This does not need to be exhaustive — it is a
#: filter for "is this a measurement", not a parser.
_UNITS = [
    "mm/year",
    "mm/yr",
    "mm/s",
    "m/s",
    "km/h",
    "m3/h",
    "l/min",
    "barg",
    "bar",
    "psig",
    "psi",
    "kpa",
    "mpa",
    "kgf/cm2",
    "degc",
    "degf",
    "°c",
    "°f",
    "celsius",
    "mm",
    "cm",
    "km",
    "µm",
    "um",
    "nm",
    "inch",
    "inches",
    "in",
    "ft",
    "m",
    "years",
    "year",
    "yr",
    "months",
    "month",
    "weeks",
    "week",
    "days",
    "day",
    "hours",
    "hour",
    "hrs",
    "hr",
    "minutes",
    "minute",
    "mins",
    "min",
    "seconds",
    "second",
    "secs",
    "sec",
    "tonnes",
    "tonne",
    "kg",
    "g",
    "lb",
    "litres",
    "litre",
    "liters",
    "liter",
    "l",
    "m3",
    "rpm",
    "hz",
    "ppm",
    "ppb",
    "%",
    "kw",
    "mw",
    "kv",
    "v",
    "a",
    "crore",
    "lakh",
    "inr",
    "usd",
    "rs",
]
_UNIT_ALTERNATION = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))

#: A number, optionally followed by a unit. The leading guard rejects a digit
#: run that is part of an identifier — the "1201" in V-1201 — and the trailing
#: one rejects a number glued to a word.
_FIGURE = re.compile(
    r"(?<![A-Za-z0-9_.\-/])"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)"
    r"(?:\s*(?P<unit>" + _UNIT_ALTERNATION + r")\b)?",
    re.IGNORECASE,
)

#: Citation markers. Their digits are references, not measurements.
_MARKER = re.compile(r"\[\d+\]")

#: A markdown table's alignment row is punctuation and digits with no meaning.
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Figure:
    """One measurement from an answer, and where it was found."""

    text: str
    """As written, e.g. "18.0 barg"."""
    value: str
    """Normalised for comparison: "18.0" and "18" both become "18"."""
    unit: str
    found: bool
    sources: list[int] = field(default_factory=list)
    """Citation numbers whose passage contains this value."""

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "value": self.value,
            "unit": self.unit,
            "found": self.found,
            "sources": self.sources,
        }


def normalise(number: str) -> str:
    """A comparable form. "1,200" -> "1200"; "9.20" -> "9.2"; "18.0" -> "18"."""
    cleaned = number.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned or "0"


def _numbers_in(text: str) -> set[str]:
    """Every number in a passage, normalised, including those inside tags."""
    return {normalise(m) for m in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text)}


def extract(text: str) -> list[Figure]:
    """The measurements an answer asserts, in the order they are written."""
    body = _TABLE_RULE.sub(" ", _MARKER.sub(" ", text))
    seen: set[tuple[str, str]] = set()
    figures: list[Figure] = []

    for match in _FIGURE.finditer(body):
        number = match.group("number")
        unit = (match.group("unit") or "").strip()
        # A measurement is a number with a unit, or a number written as a
        # decimal. Everything else is counting, numbering or prose.
        if not unit and "." not in number:
            continue
        value = normalise(number)
        key = (value, unit.lower())
        if key in seen:
            continue
        seen.add(key)
        figures.append(Figure(text=f"{number} {unit}".strip(), value=value, unit=unit, found=False))
    return figures


def verify(answer: str, sources: dict[int, str]) -> list[Figure]:
    """Check each measurement in ``answer`` against the passages it cites.

    Args:
        answer: The resolved answer, markers already numbered.
        sources: Citation number to the full text of the passage behind it.
    """
    if not answer.strip():
        return []
    by_source = {n: _numbers_in(text) for n, text in sources.items()}
    checked: list[Figure] = []
    for figure in extract(answer):
        found_in = sorted(n for n, numbers in by_source.items() if figure.value in numbers)
        checked.append(
            Figure(
                text=figure.text,
                value=figure.value,
                unit=figure.unit,
                found=bool(found_in),
                sources=found_in,
            )
        )
    return checked
