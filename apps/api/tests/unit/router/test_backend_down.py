"""A backend that is not running comes out of routing whole.

Measured on a machine where LM Studio was stopped: the planner routed to a
fallback model on the other backend, produced a worse plan, and then synthesis
routed straight back to LM Studio and killed the run with
ProviderUnavailableError. One absent process, three separate symptoms, none of
them saying "the model server is not running".
"""

from __future__ import annotations

from types import SimpleNamespace

from workbench.providers.types import Capability, ModelInfo
from workbench.router.policy import CircuitBreaker
from workbench.router.router import ModelRouter


def _model(name: str, provider: str) -> ModelInfo:
    return ModelInfo(
        logical_name=name,
        provider=provider,
        physical_id=f"physical/{name}",
        capabilities=frozenset({Capability.CHAT}),
        context_window=32768,
        approx_ram_gb=4.0,
    )


MODELS = {
    "reasoning.primary": _model("reasoning.primary", "lmstudio"),
    "reasoning.alt": _model("reasoning.alt", "lmstudio"),
    "reasoning.backup": _model("reasoning.backup", "ollama"),
    "utility.small": _model("utility.small", "lmstudio"),
}
LANES = {"reasoning": ["reasoning.primary", "reasoning.alt", "reasoning.backup"]}


def _router() -> ModelRouter:
    registry = SimpleNamespace(
        models=MODELS,
        lanes=LANES,
        get_model=MODELS.__getitem__,
        lane_candidates=lambda lane: [MODELS[n] for n in LANES.get(lane, [])],
    )
    router = ModelRouter.__new__(ModelRouter)
    router.registry = registry  # type: ignore[attr-defined]
    router.policy = SimpleNamespace(breaker=CircuitBreaker())  # type: ignore[attr-defined]
    return router


class TestNoteUnreachable:
    def test_every_model_on_that_backend_is_tripped(self) -> None:
        """Its siblings are no more reachable than it is."""
        router = _router()
        tripped = router.note_unreachable("reasoning.primary")

        assert set(tripped) == {"reasoning.primary", "reasoning.alt", "utility.small"}
        assert router.policy.breaker.is_open("reasoning.alt")
        assert router.policy.breaker.is_open("utility.small")

    def test_the_other_backend_is_left_alone(self) -> None:
        router = _router()
        router.note_unreachable("reasoning.primary")
        assert not router.policy.breaker.is_open("reasoning.backup")

    def test_it_does_not_wait_for_three_strikes(self) -> None:
        """A refused connection is an absent process, not a flaky call."""
        router = _router()
        router.note_unreachable("reasoning.primary")
        assert router.policy.breaker.is_open("reasoning.primary")


class TestStandIn:
    def test_it_is_on_a_different_backend(self) -> None:
        router = _router()
        assert router.stand_in_for("reasoning.primary") == "reasoning.backup"

    def test_none_when_the_lane_has_nothing_left(self) -> None:
        """Reported honestly rather than answered from a different lane."""
        router = _router()
        router.policy.breaker.trip("reasoning.backup", reason="also down")
        assert router.stand_in_for("reasoning.primary") is None

    def test_none_for_a_model_in_no_lane(self) -> None:
        router = _router()
        assert router.stand_in_for("utility.small") is None
