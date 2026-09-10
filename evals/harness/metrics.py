"""Metric implementations.

Kept separate from the suites and unit-tested, because a metric that is subtly
wrong is worse than no metric: it produces a number people trust and act on. The
grounding metric in the agent had exactly that problem — it scored a correctly
cited answer at 0% — and the failure was invisible until someone read the
answer next to the number.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# --- retrieval ---------------------------------------------------------------


class EmptyGoldSetError(ValueError):
    """Raised when a ranking metric is handed no relevant items.

    These metrics used to return ``1.0`` for an empty gold set, on the reading
    that "nothing was required, so nothing was missed". That reading is wrong
    here. In a retrieval eval an empty gold set never means "this query has no
    right answer" — it means *the labelled passage was not found*, which is the
    worst outcome, or the label does not match anything in the corpus, which is
    a bug in the dataset. Both scored a perfect 1.0 and were averaged into the
    headline number, so a suite could report ndcg@10 = 0.96 alongside
    recall@10 = 0.50 and no one would notice the contradiction. That is exactly
    the failure this module's docstring warns about, so the metrics now refuse
    the case and force the caller to say which of the two it is.
    """


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the relevant items that appear in the top k.

    The headline retrieval number: if the passage is not retrieved, no amount of
    model quality can recover it.

    Raises:
        EmptyGoldSetError: if ``relevant`` is empty. Score an unfound case as 0.0
            explicitly rather than passing an empty set in.
    """
    if not relevant:
        raise EmptyGoldSetError("recall_at_k needs at least one relevant item")
    found = sum(1 for item in retrieved[:k] if item in relevant)
    return found / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1/rank of the first relevant hit, 0 if none.

    Sensitive to *where* the right passage landed, which matters because the
    agent only forwards the top handful into the model's context.
    """
    for index, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Normalised discounted cumulative gain, binary relevance.

    Raises:
        EmptyGoldSetError: see :func:`recall_at_k`.
    """
    if not relevant:
        raise EmptyGoldSetError("ndcg_at_k needs at least one relevant item")
    gain = sum(
        1.0 / math.log2(index + 1)
        for index, item in enumerate(retrieved[:k], start=1)
        if item in relevant
    )
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant), k) + 1))
    return gain / ideal if ideal else 0.0


# --- text accuracy -----------------------------------------------------------


def normalise(text: str) -> str:
    """Fold text to what a reader would consider the same content.

    Case and punctuation are removed; whitespace is collapsed but *kept*, so
    "CML-04" folds to "cml04" and "cml 04" stays "cml 04" — a one-character
    difference. Keeping the space is deliberate. Word boundaries are content:
    losing them is the difference between "shell thickness survey" and
    "shellthicknesssurvey", which retrieval tokenises differently and a reader
    notices immediately. :func:`character_error_rate_ignoring_spaces` is the
    variant that forgives them, and reporting both is what showed the seed
    corpus had a segmentation problem rather than a recognition one.
    """
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", text.lower())).strip()


def character_error_rate(truth: str, hypothesis: str) -> float:
    """Levenshtein distance over characters, divided by the truth length."""
    from rapidfuzz.distance import Levenshtein

    truth_norm, hypothesis_norm = normalise(truth), normalise(hypothesis)
    if not truth_norm:
        return 0.0
    return Levenshtein.distance(truth_norm, hypothesis_norm) / len(truth_norm)


def word_error_rate(truth: str, hypothesis: str) -> float:
    from rapidfuzz.distance import Levenshtein

    truth_words, hypothesis_words = normalise(truth).split(), normalise(hypothesis).split()
    if not truth_words:
        return 0.0
    return Levenshtein.distance(truth_words, hypothesis_words) / len(truth_words)


def character_error_rate_ignoring_spaces(truth: str, hypothesis: str) -> float:
    """CER with all whitespace removed.

    Reported alongside plain CER because the two answer different questions.
    On the seed corpus the gap between them was large — recognition was already
    at 2-3% while word boundaries were being lost — and only the split made it
    obvious that the fix belonged in tokenisation, not in the recogniser.
    """
    from rapidfuzz.distance import Levenshtein

    truth_norm = re.sub(r"\s+", "", normalise(truth))
    hypothesis_norm = re.sub(r"\s+", "", normalise(hypothesis))
    if not truth_norm:
        return 0.0
    return Levenshtein.distance(truth_norm, hypothesis_norm) / len(truth_norm)


# --- classification ----------------------------------------------------------


@dataclass
class ConfusionSummary:
    accuracy: float
    per_label: dict[str, dict[str, float]]
    confusions: list[tuple[str, str, int]]

    @property
    def worst_confusion(self) -> str:
        if not self.confusions:
            return "none"
        expected, actual, count = self.confusions[0]
        return f"{expected}→{actual} ({count})"


def classification_summary(pairs: list[tuple[str, str]]) -> ConfusionSummary:
    """Accuracy plus where the errors actually go.

    A bare accuracy figure hides the distinction between a router that
    occasionally picks a slightly worse text model and one that sends images to
    a text-only model. Those are not the same failure.
    """
    if not pairs:
        return ConfusionSummary(accuracy=1.0, per_label={}, confusions=[])

    correct = sum(1 for expected, actual in pairs if expected == actual)
    labels = sorted({label for pair in pairs for label in pair})

    per_label: dict[str, dict[str, float]] = {}
    for label in labels:
        true_positive = sum(1 for e, a in pairs if e == label and a == label)
        false_positive = sum(1 for e, a in pairs if e != label and a == label)
        false_negative = sum(1 for e, a in pairs if e == label and a != label)
        precision = (
            true_positive / (true_positive + false_positive)
            if (true_positive + false_positive)
            else 1.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if (true_positive + false_negative)
            else 1.0
        )
        support = sum(1 for e, _ in pairs if e == label)
        if support:
            per_label[label] = {
                "precision": precision,
                "recall": recall,
                "support": float(support),
            }

    mistakes: dict[tuple[str, str], int] = {}
    for expected, actual in pairs:
        if expected != actual:
            mistakes[(expected, actual)] = mistakes.get((expected, actual), 0) + 1

    confusions = sorted(
        ((e, a, count) for (e, a), count in mistakes.items()),
        key=lambda row: -row[2],
    )
    return ConfusionSummary(
        accuracy=correct / len(pairs), per_label=per_label, confusions=confusions
    )


# --- latency -----------------------------------------------------------------


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Returns 0 for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]
