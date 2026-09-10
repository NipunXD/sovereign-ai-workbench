"""The OCR quality gate.

Decides whether a recognised page is good enough to index as-is, or whether it
should be re-read by a vision model. This is the judgement that keeps bad scans
from quietly poisoning retrieval: a page recognised as gibberish still indexes,
still ranks, and still gets cited — with confident nonsense attached.

Escalation is not free (a VLM pass costs seconds and memory), so the gate is
specific about why it fires rather than escalating everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from workbench.ingest.ir import BlockType, Page
from workbench.ingest.ocr.engine import OcrResult


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    TOO_FEW_WORDS = "too_few_words"
    GARBLED = "garbled"
    DRAWING = "drawing"
    HANDWRITING = "handwriting"


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    min_mean_confidence: float = 0.72
    min_words_per_page: int = 30
    #: Below this share of alphanumeric characters, the "text" is punctuation
    #: soup — high per-character confidence, no meaning.
    min_alpha_ratio: float = 0.55


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    escalate: bool
    reasons: tuple[EscalationReason, ...] = ()
    detail: str = ""
    mean_confidence: float = 1.0

    @property
    def summary(self) -> str:
        if not self.escalate:
            return f"accepted (confidence {self.mean_confidence:.0%})"
        return f"escalating to vision: {self.detail}"


def assess(
    result: OcrResult,
    *,
    thresholds: QualityThresholds | None = None,
    is_blank_page: bool = False,
    block_types: frozenset[BlockType] = frozenset(),
) -> QualityVerdict:
    """Judge one page's recognition output."""
    thresholds = thresholds or QualityThresholds()
    reasons: list[EscalationReason] = []
    details: list[str] = []

    # A genuinely blank page — a separator sheet, the back of a form — is not a
    # recognition failure and must not trigger an expensive second pass.
    if is_blank_page and result.word_count == 0:
        return QualityVerdict(escalate=False, detail="blank page", mean_confidence=1.0)

    confidence = result.mean_confidence

    if BlockType.DRAWING_ANNOTATION in block_types or BlockType.FIGURE in block_types:
        reasons.append(EscalationReason.DRAWING)
        details.append("page contains a drawing or figure")
    if BlockType.HANDWRITING in block_types:
        reasons.append(EscalationReason.HANDWRITING)
        details.append("page contains handwriting")

    if result.word_count == 0:
        reasons.append(EscalationReason.TOO_FEW_WORDS)
        details.append("no text was recognised")
    elif result.word_count < thresholds.min_words_per_page:
        reasons.append(EscalationReason.TOO_FEW_WORDS)
        details.append(
            f"only {result.word_count} words recognised "
            f"(expected at least {thresholds.min_words_per_page})"
        )

    if result.word_count and confidence < thresholds.min_mean_confidence:
        reasons.append(EscalationReason.LOW_CONFIDENCE)
        details.append(
            f"mean confidence {confidence:.0%} below {thresholds.min_mean_confidence:.0%}"
        )

    if result.word_count and result.alpha_ratio < thresholds.min_alpha_ratio:
        reasons.append(EscalationReason.GARBLED)
        details.append(f"only {result.alpha_ratio:.0%} of characters are alphanumeric")

    return QualityVerdict(
        escalate=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        detail="; ".join(details),
        mean_confidence=confidence,
    )


def page_needs_ocr(page: Page) -> bool:
    """Whether a page carries no usable native text and must be recognised."""
    return not page.blocks or page.word_count == 0
