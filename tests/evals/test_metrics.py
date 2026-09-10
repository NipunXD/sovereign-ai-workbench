"""Unit tests for the eval metrics.

These exist because the metrics are the thing everything else is judged by, so
a bug here is invisible: it does not crash, it just reports a number that is
wrong in a plausible direction. Two of the cases below are regressions for
exactly that — an empty gold set once scored a perfect 1.0, which let the
retrieval suite report ndcg@10 = 0.96 while recall@10 was 0.50.
"""

from __future__ import annotations

import pytest

from evals.harness.metrics import (
    EmptyGoldSetError,
    character_error_rate,
    character_error_rate_ignoring_spaces,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)


class TestRecallAtK:
    def test_counts_only_the_top_k(self) -> None:
        retrieved = ["a", "b", "c", "d"]
        assert recall_at_k(retrieved, {"a", "d"}, 2) == 0.5
        assert recall_at_k(retrieved, {"a", "d"}, 4) == 1.0

    def test_missing_everything_scores_zero(self) -> None:
        assert recall_at_k(["x", "y"], {"a"}, 2) == 0.0

    def test_empty_gold_set_is_a_labelling_bug_not_a_perfect_score(self) -> None:
        with pytest.raises(EmptyGoldSetError):
            recall_at_k(["a", "b"], set(), 2)


class TestReciprocalRank:
    def test_is_one_over_the_first_hit(self) -> None:
        assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
        assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)

    def test_takes_the_first_of_several(self) -> None:
        assert reciprocal_rank(["a", "b", "c"], {"b", "c"}) == 0.5

    def test_no_hit_scores_zero(self) -> None:
        assert reciprocal_rank(["a", "b"], {"z"}) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking_scores_one(self) -> None:
        assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, 3) == pytest.approx(1.0)

    def test_rewards_the_higher_rank(self) -> None:
        early = ndcg_at_k(["a", "x", "y"], {"a"}, 3)
        late = ndcg_at_k(["x", "y", "a"], {"a"}, 3)
        assert early > late

    def test_empty_gold_set_is_refused(self) -> None:
        # The regression: this used to return 1.0, so every case whose gold
        # passage was not found was averaged in as a perfect score.
        with pytest.raises(EmptyGoldSetError):
            ndcg_at_k(["a", "b"], set(), 2)


class TestCharacterErrorRate:
    def test_identical_text_is_zero(self) -> None:
        assert character_error_rate("Vessel V-1201", "Vessel V-1201") == 0.0

    def test_normalisation_forgives_case_and_punctuation(self) -> None:
        assert character_error_rate("CML-04", "CML.04!") == 0.0

    def test_a_lost_word_boundary_still_counts_as_an_error(self) -> None:
        # normalise() keeps spaces, so "cml04" vs "cml 04" is one insertion in
        # five characters. Forgiving that here would leave nothing to compare
        # the space-ignoring variant against.
        assert character_error_rate("CML-04", "cml 04") == pytest.approx(0.2)

    def test_counts_substituted_characters(self) -> None:
        # One digit wrong in eight normalised characters ("thick920" style).
        assert character_error_rate("9.20 mm", "9.30 mm") == pytest.approx(1 / 6)

    def test_ignoring_spaces_isolates_recognition_from_segmentation(self) -> None:
        # Run-together words: every character is right, only the boundaries
        # are lost. This is the split that showed OCR error was 1.5%, not 6.4%.
        truth, hypothesis = "shell thickness survey", "shellthicknesssurvey"
        assert character_error_rate(truth, hypothesis) > 0
        assert character_error_rate_ignoring_spaces(truth, hypothesis) == 0.0

    def test_empty_truth_does_not_divide_by_zero(self) -> None:
        assert character_error_rate("", "anything") == 0.0


class TestPercentile:
    def test_is_nearest_rank_not_interpolated(self) -> None:
        # Deliberately reports a value that was actually observed, rather than
        # an average of two that never happened.
        assert percentile([0.0, 10.0], 0.5) == 0.0
        assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0

    def test_endpoints(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        assert percentile(values, 0.0) == 1.0
        assert percentile(values, 1.0) == 4.0

    def test_empty_is_zero_not_an_error(self) -> None:
        # A suite that routed nothing to a stage has no samples for it; that is
        # a legitimate zero, unlike an empty gold set.
        assert percentile([], 0.95) == 0.0


class TestMetricDirection:
    """Which way an arrow should point.

    A regression here is invisible in the worst way: the number is right, the
    delta is right, and only the colour lies. It happened — renaming the
    router's latency metric left the hand-maintained list naming the old one,
    so a slower fast path rendered as a green improvement.
    """

    def test_the_suite_threshold_decides(self) -> None:
        from evals.harness.report import prefers_higher

        # A suite asserting "<=" has already said smaller is better.
        assert not prefers_higher("fast_path_p95_ms", ("<=", 25.0))
        assert prefers_higher("recall_at_10", (">=", 0.85))
        # Even a name that looks like an error rate defers to the threshold.
        assert prefers_higher("refusal_rate", (">=", 0.75))

    def test_unbounded_durations_are_lower_is_better(self) -> None:
        from evals.harness.report import prefers_higher

        for metric in ("classifier_p95_ms", "fast_path_p50_ms", "p95_latency_s", "mean_s"):
            assert not prefers_higher(metric, None), metric

    def test_unbounded_error_rates_are_lower_is_better(self) -> None:
        from evals.harness.report import prefers_higher

        for metric in ("cer", "wer", "cer_no_spaces", "failed_run_rate"):
            assert not prefers_higher(metric, None), metric

    def test_anything_else_defaults_to_higher_is_better(self) -> None:
        from evals.harness.report import prefers_higher

        assert prefers_higher("mean_confidence", None)
        assert prefers_higher("hybrid_share", None)
