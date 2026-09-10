"""Routing accuracy and cost.

Two numbers matter and they are different questions. Accuracy asks whether the
right lane was chosen; the stage distribution asks how much that cost. A router
that is accurate only because it asks a model every time has not earned its
place — the cascade exists so that most traffic never reaches stage 2.

The vision cases are the ones worth watching. Sending an image to a text-only
model produces a confident answer about a picture the model never saw, which is
the worst failure mode available here, so those are asserted separately from
overall accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.metrics import classification_summary, percentile
from evals.harness.registry import CaseResult, SuiteResult, suite

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "router_labels.jsonl"


@suite("router")
async def run() -> SuiteResult:
    from workbench.providers.registry import ModelRegistry
    from workbench.router.router import ModelRouter, RouteRequest
    from workbench.settings import get_settings

    settings = get_settings()
    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]

    try:
        registry = ModelRegistry(settings.models_manifest)
        await registry.probe_availability()
        router = ModelRouter(registry, config_path=settings.router_config)
    except Exception as exc:
        return SuiteResult(suite="router", skipped=f"router unavailable: {exc}")

    results: list[CaseResult] = []
    pairs: list[tuple[str, str]] = []
    # Split by stage. The cascade's whole point is that stages 0 and 1 are
    # deterministic and effectively free while stage 2 is a deliberate model
    # call, so a single percentile over both measures nothing: with 2 of 24
    # cases reaching the classifier, p95 lands on a model call and reports
    # ~2000 ms as though routing were slow. It is the *share* of traffic
    # that reaches the model that should be bounded, not the cost of a call
    # we chose to make.
    fast_latencies: list[float] = []
    classifier_latencies: list[float] = []
    stage_counts = {0: 0, 1: 0, 2: 0}
    vision_correct = 0
    vision_total = 0

    for case in cases:
        decision = await router.route(
            RouteRequest(
                text=case["text"],
                has_images=bool(case.get("has_images")),
                estimated_tokens=int(case.get("estimated_tokens", 0)),
            )
        )
        expected, actual = case["lane"], decision.lane.value
        pairs.append((expected, actual))
        if decision.stage_decided >= 2:
            classifier_latencies.append(decision.decide_latency_ms)
        else:
            fast_latencies.append(decision.decide_latency_ms)
        stage_counts[decision.stage_decided] = stage_counts.get(decision.stage_decided, 0) + 1

        if case.get("has_images"):
            vision_total += 1
            # The model chosen must actually be able to see, not merely be in
            # the vision lane.
            if "vision" in decision.model.capabilities:
                vision_correct += 1

        results.append(
            CaseResult(
                case_id=case["id"],
                passed=expected == actual,
                metrics={"decide_ms": decision.decide_latency_ms, "stage": decision.stage_decided},
                detail="" if expected == actual else f"expected {expected}, routed to {actual}",
                expected=expected,
                actual=actual,
            )
        )

    await registry.aclose()
    summary = classification_summary(pairs)
    total = len(cases) or 1

    return SuiteResult(
        suite="router",
        cases=results,
        metrics={
            "lane_accuracy": summary.accuracy,
            # An image reaching a model that cannot see is the failure this
            # number exists to make impossible to ship.
            "vision_capability_rate": vision_correct / vision_total if vision_total else 1.0,
            "stage0_share": stage_counts.get(0, 0) / total,
            "stage2_share": stage_counts.get(2, 0) / total,
            "fast_path_p50_ms": percentile(fast_latencies, 0.50),
            "fast_path_p95_ms": percentile(fast_latencies, 0.95),
            "classifier_p95_ms": percentile(classifier_latencies, 0.95),
            "cases": float(total),
        },
        thresholds={
            "lane_accuracy": (">=", 0.85),
            "vision_capability_rate": (">=", 1.0),
            # The deterministic path is what runs on nearly every request, so
            # it is bounded tightly. Measured p95 is well under a millisecond;
            # 25 ms leaves room for a loaded machine without letting a genuine
            # regression through.
            "fast_path_p95_ms": ("<=", 25.0),
            # And the escape hatch is bounded by *frequency*, not latency: if a
            # change starts pushing most traffic to the classifier, routing
            # stops being free even though every individual call is still fast.
            "stage2_share": ("<=", 0.25),
        },
    )
