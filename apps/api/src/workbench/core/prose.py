"""Turning the model's mathematical notation into text a report can carry.

Asked for arithmetic, a model reaches for LaTeX — ``$\\frac{3.3}{6} = 0.55\\,
\\text{mm/year}$``. Nothing downstream renders it. The workbench shows the
answer as markdown and prints it into Word, so what an inspection engineer
actually reads is the backslashes, and a figure wrapped in typesetting is a
figure they cannot check at a glance.

Asking the model not to use it is worth doing and is not enough: the prompt is
a request and this is a guarantee. Everything here rewrites notation into the
same expression written plainly — no value is altered, and anything not
recognised is left exactly as it was rather than mangled into something that
looks like prose but is not.
"""

from __future__ import annotations

import re

#: \frac{a}{b} -> a / b. Applied repeatedly so a nested fraction unwraps from
#: the inside out rather than leaving half of itself behind.
_FRACTION = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")

#: \text{mm/year}, \mathrm{...} — a wrapper around ordinary words.
_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathit|mathbf|operatorname)\s*\{([^{}]*)\}")

#: \sqrt{x} has no plain equivalent that stays unambiguous, so it keeps its name.
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")

#: Symbols with an exact plain reading.
_SYMBOLS: tuple[tuple[str, str], ...] = (
    (r"\approx", "≈"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\div", "÷"),
    (r"\pm", "±"),
    (r"\leq", "≤"),
    (r"\geq", "≥"),
    (r"\neq", "≠"),
    (r"\rightarrow", "→"),
    (r"\to", "→"),
    (r"\degree", "°"),
    (r"\%", "%"),
    (r"\,", " "),
    (r"\;", " "),
    (r"\:", " "),
    (r"\!", ""),
    (r"\\", " "),
)

#: $...$ and \( ... \) and \[ ... \] — the delimiters themselves.
_INLINE_MATH = re.compile(r"\$\$?(.+?)\$\$?", re.DOTALL)
_PAREN_MATH = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET_MATH = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def plain_maths(text: str) -> str:
    """Rewrite LaTeX notation as the same expression in plain text."""
    if not text or ("\\" not in text and "$" not in text):
        return text

    cleaned = text
    for _ in range(4):  # nested fractions, innermost first
        replaced = _FRACTION.sub(r"\1 / \2", cleaned)
        if replaced == cleaned:
            break
        cleaned = replaced

    cleaned = _TEXT_WRAPPER.sub(r"\1", cleaned)
    cleaned = _SQRT.sub(r"√(\1)", cleaned)
    for symbol, plain in _SYMBOLS:
        cleaned = cleaned.replace(symbol, plain)

    # Delimiters last: the contents have already been rewritten, so removing
    # the wrapper leaves a readable expression rather than a bare fragment.
    # Stripped as they are unwrapped — "$ 3.3 / 6 $" sat inside its delimiters
    # with a space at each end, and those spaces are the delimiter's, not the
    # sentence's.
    def unwrap(match: re.Match[str]) -> str:
        return match.group(1).strip()

    cleaned = _BRACKET_MATH.sub(unwrap, cleaned)
    cleaned = _PAREN_MATH.sub(unwrap, cleaned)
    cleaned = _INLINE_MATH.sub(unwrap, cleaned)

    # Collapse the spacing the substitutions leave behind, without touching
    # line structure — a report's paragraphs are load-bearing.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return re.sub(r" +([,.;:)])", r"\1", cleaned)
