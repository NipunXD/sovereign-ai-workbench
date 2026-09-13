"""Model selection within a lane, and the circuit breaker that guards it.

Once the router has picked a lane, this decides *which* candidate actually runs.
Two rules matter more than the rest:

* **Capability filtering is structural.** A model without the vision capability
  can never be selected for a vision request. Not "unlikely" — impossible, by
  construction, because it is filtered out before scoring begins.
* **Residency has a price.** A cold load costs several seconds on this hardware.
  The ``loaded_then_quality`` preference converts that measured cost into a
  score bonus, so an adequate resident model beats a marginally better one that
  would have to be paged in.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from workbench.core.errors import ModelUnavailableError
from workbench.core.logging import get_logger
from workbench.providers.types import Capability, Lane, ModelInfo

log = get_logger(__name__)


class ResidencyView(Protocol):
    """The slice of the residency manager selection needs."""

    def is_resident(self, logical_name: str) -> bool: ...
    def swap_cost_s(self, logical_name: str) -> float: ...


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float | None = None
    first_failure_at: float = 0.0


class CircuitBreaker:
    """Stops routing to a model that keeps failing.

    Without this, a backend that is down turns every request into a slow walk
    through the whole candidate list before failing anyway.
    """

    def __init__(
        self, *, failure_threshold: int = 3, window_s: float = 60.0, cooldown_s: float = 120.0
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._state: dict[str, _BreakerState] = defaultdict(_BreakerState)

    def is_open(self, logical_name: str) -> bool:
        state = self._state[logical_name]
        if state.opened_at is None:
            return False
        if time.monotonic() - state.opened_at >= self.cooldown_s:
            # Cooldown elapsed: allow one trial request through.
            self._state[logical_name] = _BreakerState()
            return False
        return True

    def record_success(self, logical_name: str) -> None:
        self._state[logical_name] = _BreakerState()

    def record_failure(self, logical_name: str) -> None:
        state = self._state[logical_name]
        now = time.monotonic()
        if state.failures == 0 or now - state.first_failure_at > self.window_s:
            state.failures, state.first_failure_at = 1, now
        else:
            state.failures += 1
        if state.failures >= self.failure_threshold:
            state.opened_at = now
            log.warning(
                "circuit_breaker_opened",
                model=logical_name,
                failures=state.failures,
                cooldown_s=self.cooldown_s,
            )

    def trip(self, logical_name: str, *, reason: str) -> None:
        """Open the breaker at once, without waiting for the strike count.

        Three strikes is the right shape for a model that answers badly under
        load. It is the wrong shape for a backend that is not running: every
        model it serves will refuse every connection, and counting to three on
        each of them means the run walks the whole candidate list before it
        gives up. A refused connection is not a flaky call, it is an absent
        process.
        """
        state = self._state[logical_name]
        state.failures = max(state.failures, self.failure_threshold)
        state.opened_at = time.monotonic()
        state.first_failure_at = state.opened_at
        log.warning("circuit_breaker_tripped", model=logical_name, reason=reason)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "failures": state.failures,
                "open": self.is_open(name),
            }
            for name, state in self._state.items()
            if state.failures
        }


@dataclass(frozen=True, slots=True)
class SelectionRequirements:
    """Hard constraints a candidate must satisfy to be eligible."""

    capabilities: frozenset[Capability] = frozenset()
    min_context_window: int = 0

    @classmethod
    def for_lane(
        cls,
        lane: Lane,
        *,
        estimated_tokens: int = 0,
        needs_tools: bool = False,
        needs_json: bool = False,
    ) -> SelectionRequirements:
        required: set[Capability] = set()
        if lane is Lane.VISION:
            required.add(Capability.VISION)
        elif lane is Lane.EMBEDDING:
            required.add(Capability.EMBED)
        else:
            required.add(Capability.CHAT)
        if needs_tools:
            required.add(Capability.TOOLS)
        if needs_json:
            required.add(Capability.JSON)
        # Leave headroom for the response; a prompt that exactly fills the
        # window leaves no room to answer.
        return cls(
            capabilities=frozenset(required),
            min_context_window=int(estimated_tokens * 1.25) if estimated_tokens else 0,
        )


@dataclass(frozen=True, slots=True)
class Selection:
    """The chosen model plus the reasoning behind it."""

    model: ModelInfo
    score: float
    resident: bool
    swap_cost_s: float
    alternatives: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    #: True when the prompt exceeds even the largest available context window.
    #: The caller must map-reduce (see the ``doc.summarize_long`` tool) rather
    #: than sending the whole thing and having the backend truncate it silently.
    requires_chunking: bool = False


class SelectionPolicy:
    """Scores and ranks the candidates in a lane."""

    def __init__(
        self,
        *,
        prefer: str = "loaded_then_quality",
        residency_bonus_per_second: float = 0.15,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.prefer = prefer
        self.residency_bonus_per_second = residency_bonus_per_second
        self.breaker = breaker or CircuitBreaker()

    def select(
        self,
        candidates: list[ModelInfo],
        requirements: SelectionRequirements,
        *,
        residency: ResidencyView | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> Selection:
        """Pick the best eligible candidate, or explain why none qualify."""
        rejected: dict[str, str] = {}
        eligible: list[ModelInfo] = []

        for info in candidates:
            if info.logical_name in exclude:
                rejected[info.logical_name] = "already attempted in this request"
                continue
            missing = requirements.capabilities - info.capabilities
            if missing:
                rejected[info.logical_name] = (
                    f"missing capability: {', '.join(sorted(c.value for c in missing))}"
                )
                continue
            if requirements.min_context_window > info.context_window:
                rejected[info.logical_name] = (
                    f"context window {info.context_window} < required "
                    f"{requirements.min_context_window}"
                )
                continue
            if self.breaker.is_open(info.logical_name):
                rejected[info.logical_name] = "circuit breaker open after repeated failures"
                continue
            eligible.append(info)

        requires_chunking = False
        if not eligible and requirements.min_context_window:
            # Nothing has a big enough window. Failing here would be wrong: a
            # long document is exactly what the long-context lane exists for.
            # Fall back to the widest-window model that meets every *other*
            # requirement and tell the caller to map-reduce.
            widened = [
                info
                for info in candidates
                if info.logical_name not in exclude
                and requirements.capabilities <= info.capabilities
                and not self.breaker.is_open(info.logical_name)
            ]
            if widened:
                widest = max(info.context_window for info in widened)
                eligible = [info for info in widened if info.context_window == widest]
                requires_chunking = True
                for info in eligible:
                    rejected.pop(info.logical_name, None)
                log.info(
                    "context_overflow_will_chunk",
                    required=requirements.min_context_window,
                    largest_window=widest,
                )

        if not eligible:
            raise ModelUnavailableError(
                "no model satisfies this request",
                rejected=rejected,
                required_capabilities=sorted(c.value for c in requirements.capabilities),
            )

        scored = [(self._score(info, residency), info) for info in eligible]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = scored[0]

        return Selection(
            model=best,
            score=best_score,
            resident=bool(residency and residency.is_resident(best.logical_name)),
            swap_cost_s=residency.swap_cost_s(best.logical_name) if residency else 0.0,
            alternatives=[info.logical_name for _, info in scored[1:]],
            rejected=rejected,
            requires_chunking=requires_chunking,
        )

    def _score(self, info: ModelInfo, residency: ResidencyView | None) -> float:
        """Higher is better. Tiers are 1-best, so they are inverted here."""
        quality = 5.0 - info.quality_tier
        speed = 5.0 - info.speed_tier

        if self.prefer == "quality_tier":
            base = quality * 2.0 + speed * 0.25
        elif self.prefer == "speed_tier":
            base = speed * 2.0 + quality * 0.25
        else:  # loaded_then_quality
            base = quality * 1.0 + speed * 0.5

        if self.prefer == "loaded_then_quality" and residency is not None:
            # Charge each candidate for the wall-clock cost of paging it in. A
            # resident model costs nothing; a 9-second cold load surrenders more
            # than a full quality tier, which is the intended trade.
            base -= residency.swap_cost_s(info.logical_name) * self.residency_bonus_per_second
            if residency.is_resident(info.logical_name):
                base += 1.0
        return base
