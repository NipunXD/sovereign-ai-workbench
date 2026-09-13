"""OCR behind a protocol, with RapidOCR as the default engine.

RapidOCR rather than Tesseract: it installs as a pip wheel with no system
library to version-match on an air-gapped rack, runs on CPU, returns per-line
boxes and confidences, and handles rotated and degraded text better. Tesseract
remains available behind the same protocol for sites that already standardise
on it.

Confidence is the output that matters most. It decides whether a page is
escalated to a vision model, it is carried onto every citation so a reader can
see when an answer rests on a bad scan, and it penalises those chunks during
ranking.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

from workbench.core.ir import BBox
from workbench.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    bbox: BBox
    confidence: float


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""
    duration_ms: int = 0

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def word_count(self) -> int:
        return sum(len(line.text.split()) for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        """Word-weighted, so a confidently-read page number cannot lift a page
        whose body text the recogniser struggled with."""
        weighted = [(line.confidence, len(line.text.split())) for line in self.lines]
        weighted = [(c, w) for c, w in weighted if w]
        if not weighted:
            return 0.0
        total = sum(w for _, w in weighted)
        return sum(c * w for c, w in weighted) / total

    @property
    def alpha_ratio(self) -> float:
        """Share of characters that are letters or digits.

        A page of recognised punctuation soup scores high confidence per
        character while being meaningless, and this is what catches it.
        """
        text = self.text
        if not text:
            return 0.0
        return sum(1 for ch in text if ch.isalnum() or ch.isspace()) / len(text)


#: Long runs of letters with internal capitals — "CrudeFeedSurgeDrum" — are the
#: dominant OCR spacing failure on dense scans. Recognition itself is accurate
#: (measured ~2-3% character error ignoring spaces); it is the word boundaries
#: that are lost, and a run-together token breaks retrieval outright because
#: "surge drum" no longer matches the text that contains it.
_RUN_TOGETHER = re.compile(r"\b(?=\w*[a-z])(?=\w*[A-Z])[A-Za-z]{12,}\b")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
#: A digit pressed against a letter: "pressure18.0barg", "V-1201CrudeFeed".
_DIGIT_LETTER = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[A-Z][a-z])")

#: Punctuation glued to the next word — "Drum.Design", "barg.Material",
#: "survey,down". This is the single largest remaining source of word error:
#: on the seed corpus it alone accounted for 26 points of the 45% WER, while
#: the character error ignoring spaces stayed at 1.5%. The recogniser reads the
#: page almost perfectly and then loses the gaps.
#:
#: The capital must be followed by a lowercase letter, so a word is split and a
#: run of initials is not: "Drum.Design" opens up, "U.S.A." does not. A digit
#: after the stop is left alone, which is what keeps "Gr.70" and "18.0" intact,
#: and a lowercase one is left alone too, so "report.pdf" and an email address
#: survive.
_PUNCT_WORD = re.compile(r"(?<=[.:;,])(?=[A-Z][a-z])")
#: The same join in front of an equipment tag — "Equipment:V-1201". Tags are
#: capitals and digits, so the rule above deliberately misses them, and a tag
#: welded to the word before it is exactly what breaks a search for the tag.
_PUNCT_TAG = re.compile(r"(?<=[:.])(?=[A-Z]{1,4}-\d)")
#: A comma against a lowercase word: "12.50mm,down". Unlike a full stop, a
#: comma immediately followed by a letter is always a lost space.
_COMMA_WORD = re.compile(r"(?<=,)(?=[a-z])")
#: A unit welded to its reading: "18.0barg", "120degC". Ordinals are excluded
#: so that "1st" and "2nd" are not pulled apart, and two letters are required
#: so "4h" and "0x1f" are left alone.
_NUMBER_UNIT = re.compile(r"(?<=\d)(?=(?!st\b|nd\b|rd\b|th\b)[a-z]{2,})")


def split_run_together(text: str) -> str:
    """Restore spaces the recogniser dropped between words.

    Every rule here only ever *inserts* a space; none rewrites a character. That
    is what makes the effect measurable rather than a matter of taste — on the
    seed corpus these rules take word error from 44.7% to 19.0% while the
    character error ignoring spaces does not move at all, which is the proof
    that the recogniser was reading the page correctly and only losing the gaps.

    Deliberately conservative throughout. A dictionary segmenter would read
    better prose out of the easy cases and mangle the tag conventions and unit
    strings this corpus is full of, and a tag that has been "corrected" is
    worse than one that was never touched.
    """

    def split(match: re.Match[str]) -> str:
        return _CAMEL_BOUNDARY.sub(" ", match.group(0))

    text = _RUN_TOGETHER.sub(split, text)
    text = _DIGIT_LETTER.sub(" ", text)
    text = _PUNCT_WORD.sub(" ", text)
    text = _PUNCT_TAG.sub(" ", text)
    text = _COMMA_WORD.sub(" ", text)
    return _NUMBER_UNIT.sub(" ", text)


@runtime_checkable
class OCREngine(Protocol):
    name: str

    def recognise(self, image: np.ndarray) -> OcrResult: ...


class RapidOcrEngine:
    """ONNX-based recognition. No system dependencies."""

    name = "rapidocr"

    def __init__(self, **options: Any) -> None:
        self._engine: Any = None
        self._options = options

    def _lazy(self) -> Any:
        # Loading the ONNX models costs a couple of seconds and a few hundred
        # megabytes, so it happens on first use rather than at import.
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR(**self._options)
        return self._engine

    def recognise(self, image: np.ndarray) -> OcrResult:
        started = time.perf_counter()
        try:
            raw, _ = self._lazy()(image)
        except Exception as exc:
            log.warning("ocr_failed", engine=self.name, error=str(exc))
            return OcrResult(
                engine=self.name, duration_ms=int((time.perf_counter() - started) * 1000)
            )

        height, width = image.shape[:2]
        lines: list[OcrLine] = []
        for entry in raw or []:
            try:
                box, text, confidence = entry[0], entry[1], float(entry[2])
            except (IndexError, TypeError, ValueError):
                continue
            if not str(text).strip():
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            lines.append(
                OcrLine(
                    text=split_run_together(str(text)),
                    bbox=BBox.from_pixels((min(xs), min(ys), max(xs), max(ys)), width, height),
                    confidence=confidence,
                )
            )

        return OcrResult(
            lines=lines,
            engine=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class TesseractEngine:
    """Alternative engine for sites already standardised on Tesseract."""

    name = "tesseract"

    def __init__(self, languages: str = "eng") -> None:
        self.languages = languages

    def recognise(self, image: np.ndarray) -> OcrResult:
        started = time.perf_counter()
        try:
            import pytesseract
        except ImportError as exc:
            from workbench.core.errors import ConfigurationError

            raise ConfigurationError(
                "the tesseract engine is selected but pytesseract is not installed; "
                "set ocr.engine to 'rapidocr' in config/ingest.yaml"
            ) from exc

        data = pytesseract.image_to_data(
            image, lang=self.languages, output_type=pytesseract.Output.DICT
        )
        height, width = image.shape[:2]
        lines: list[OcrLine] = []
        for index, text in enumerate(data["text"]):
            if not text.strip():
                continue
            confidence = float(data["conf"][index])
            if confidence < 0:  # tesseract reports -1 for non-text regions
                continue
            x, y = data["left"][index], data["top"][index]
            lines.append(
                OcrLine(
                    text=text,
                    bbox=BBox.from_pixels(
                        (x, y, x + data["width"][index], y + data["height"][index]), width, height
                    ),
                    confidence=confidence / 100.0,
                )
            )
        return OcrResult(
            lines=lines,
            engine=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def build_engine(name: str, **options: Any) -> OCREngine:
    if name == "tesseract":
        return TesseractEngine(**options)
    if name == "rapidocr":
        return RapidOcrEngine()
    from workbench.core.errors import ConfigurationError

    raise ConfigurationError(f"unknown OCR engine '{name}'")
