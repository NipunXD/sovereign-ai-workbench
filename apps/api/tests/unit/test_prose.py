"""LaTeX in an answer nobody renders.

Asked for arithmetic the model writes ``$\\frac{3.3}{6}$``, and the workbench
shows markdown and prints into Word — so the reader gets the backslashes. The
rule under test is that the expression survives and the notation does not.
"""

from __future__ import annotations

import pytest

from workbench.core.prose import plain_maths


class TestNotationBecomesText:
    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            # Straight from a real answer.
            (r"$ \frac{3.3}{6} = 0.55 \, \text{mm/year} $", "3.3 / 6 = 0.55 mm/year"),
            (r"$ \frac{1.2}{0.55} \approx 2.18 \, \text{years} $", "1.2 / 0.55 ≈ 2.18 years"),
            (r"\(P \times R\)", "P × R"),
            (r"12.5 \text{ mm}", "12.5  mm".replace("  ", " ")),
            (r"$t_{min} \geq 8.0$", "t_{min} ≥ 8.0"),
        ],
    )
    def test_the_expression_survives_the_notation(self, written: str, expected: str) -> None:
        assert plain_maths(written) == expected

    def test_a_nested_fraction_unwraps_completely(self) -> None:
        assert "\\frac" not in plain_maths(r"$\frac{\frac{1}{2}}{3}$")

    def test_no_digit_is_altered(self) -> None:
        out = plain_maths(r"$\frac{3.30}{6} = 0.5500 \text{ mm/year}$")
        for number in ("3.30", "6", "0.5500"):
            assert number in out


class TestOrdinaryTextIsUntouched:
    @pytest.mark.parametrize(
        "text",
        [
            "The corrosion rate is 0.55 mm/year against an 8.0 mm minimum.",
            "Cost was $142 crore for the turnaround.",
            "",
            "See C:\\reports\\v1201 for the scan.",
        ],
    )
    def test_text_with_no_maths_comes_back_identical(self, text: str) -> None:
        assert plain_maths(text) == text
