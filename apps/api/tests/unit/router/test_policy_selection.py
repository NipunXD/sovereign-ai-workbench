"""Model selection: capability filtering, residency pricing, circuit breaking."""

from __future__ import annotations

import pytest

from workbench.core.errors import ModelUnavailableError
from workbench.providers.types import Capability, ModelInfo
from workbench.router.policy import (
    CircuitBreaker,
    SelectionPolicy,
    SelectionRequirements,
)


def make_model(
    name: str,
    *,
    capabilities: set[Capability] | None = None,
    context_window: int = 32768,
    quality_tier: int = 1,
    speed_tier: int = 2,
    ram_gb: float = 6.0,
) -> ModelInfo:
    return ModelInfo(
        logical_name=name,
        provider="test",
        physical_id=f"physical/{name}",
        capabilities=frozenset(capabilities or {Capability.CHAT}),
        context_window=context_window,
        approx_ram_gb=ram_gb,
        quality_tier=quality_tier,
        speed_tier=speed_tier,
    )


class FakeResidency:
    """Stands in for the residency manager with a fixed view of memory."""

    def __init__(self, resident: set[str], swap_costs: dict[str, float] | None = None) -> None:
        self._resident = resident
        self._costs = swap_costs or {}

    def is_resident(self, logical_name: str) -> bool:
        return logical_name in self._resident

    def swap_cost_s(self, logical_name: str) -> float:
        if logical_name in self._resident:
            return 0.0
        return self._costs.get(logical_name, 6.0)


def test_model_without_capability_is_never_selected() -> None:
    """Structural exclusion, not a scoring penalty."""
    policy = SelectionPolicy()
    candidates = [
        make_model("text-only"),
        make_model("sees", capabilities={Capability.CHAT, Capability.VISION}),
    ]
    selection = policy.select(
        candidates, SelectionRequirements(capabilities=frozenset({Capability.VISION}))
    )
    assert selection.model.logical_name == "sees"
    assert "text-only" in selection.rejected


def test_no_eligible_candidate_raises_with_reasons() -> None:
    """Failure must explain itself; a bare 'unavailable' is undebuggable."""
    policy = SelectionPolicy()
    with pytest.raises(ModelUnavailableError) as excinfo:
        policy.select(
            [make_model("text-only")],
            SelectionRequirements(capabilities=frozenset({Capability.VISION})),
        )
    assert "text-only" in excinfo.value.extra["rejected"]


def test_context_window_too_small_is_rejected() -> None:
    policy = SelectionPolicy()
    candidates = [make_model("small", context_window=4096), make_model("big", context_window=131072)]
    selection = policy.select(
        candidates, SelectionRequirements(min_context_window=40000)
    )
    assert selection.model.logical_name == "big"


def test_resident_model_beats_marginally_better_cold_model() -> None:
    """The central trade: several seconds of load is worth a quality tier.

    'good' is one tier better but cold; 'okay' is loaded. With
    loaded_then_quality, the resident model should win.
    """
    policy = SelectionPolicy(prefer="loaded_then_quality", residency_bonus_per_second=0.15)
    candidates = [
        make_model("good", quality_tier=1),
        make_model("okay", quality_tier=2),
    ]
    residency = FakeResidency(resident={"okay"}, swap_costs={"good": 9.0})
    selection = policy.select(candidates, SelectionRequirements(), residency=residency)
    assert selection.model.logical_name == "okay"
    assert selection.resident is True


def test_quality_profile_ignores_residency() -> None:
    """The quality profile is supposed to pay the load cost."""
    policy = SelectionPolicy(prefer="quality_tier")
    candidates = [make_model("good", quality_tier=1), make_model("okay", quality_tier=3)]
    residency = FakeResidency(resident={"okay"}, swap_costs={"good": 9.0})
    selection = policy.select(candidates, SelectionRequirements(), residency=residency)
    assert selection.model.logical_name == "good"


def test_excluded_models_are_skipped_on_retry() -> None:
    """A model that just failed must not be retried within the same request."""
    policy = SelectionPolicy()
    candidates = [make_model("first"), make_model("second", quality_tier=3)]
    selection = policy.select(
        candidates, SelectionRequirements(), exclude=frozenset({"first"})
    )
    assert selection.model.logical_name == "second"


def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=3, window_s=60, cooldown_s=120)
    assert not breaker.is_open("flaky")
    for _ in range(3):
        breaker.record_failure("flaky")
    assert breaker.is_open("flaky")


def test_circuit_breaker_resets_on_success() -> None:
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure("flaky")
    breaker.record_failure("flaky")
    breaker.record_success("flaky")
    breaker.record_failure("flaky")
    assert not breaker.is_open("flaky"), "success should have cleared the count"


def test_open_breaker_removes_model_from_selection() -> None:
    policy = SelectionPolicy()
    for _ in range(3):
        policy.breaker.record_failure("broken")
    selection = policy.select(
        [make_model("broken", quality_tier=1), make_model("working", quality_tier=3)],
        SelectionRequirements(),
    )
    assert selection.model.logical_name == "working"
    assert "circuit breaker" in selection.rejected["broken"]
