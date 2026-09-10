"""The multi-model router.

Three stages, each able to short-circuit, ordered by cost:

  stage 0  deterministic overrides    microseconds   ~70% of real traffic
  stage 1  lexical feature scoring    sub-millisecond
  stage 2  small-model classifier     50-150ms, cached permanently after

The design principle is that anything which *must* be right is decided
deterministically in stage 0, never by a model. An attached image forces the
vision lane; an embedding caller gets the embedding lane; an agent node that
knows its own intent declares it. Stage 2 exists only for genuinely ambiguous
free-text, and its answers are cached by prompt hash so no prompt is ever
classified twice.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from workbench.core.hashing import digest
from workbench.core.logging import get_logger
from workbench.providers.registry import ModelRegistry
from workbench.providers.types import (
    ChatMessage,
    GenerationRequest,
    Lane,
    ModelInfo,
)
from workbench.router.features import PromptFeatures, extract
from workbench.router.policy import (
    CircuitBreaker,
    ResidencyView,
    SelectionPolicy,
    SelectionRequirements,
)

log = get_logger(__name__)

#: JSON schema constraining the stage-2 classifier's reply.
_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lane": {
            "type": "string",
            "enum": [lane.value for lane in Lane if lane is not Lane.EMBEDDING],
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["lane", "confidence", "reason"],
}

_CLASSIFIER_PROMPT = """You classify a user request into exactly one processing lane.

reasoning  - analysis, comparison, explanation, general question answering
coding     - writing, debugging or explaining code and scripts
long_ctx   - summarising or reading across whole documents
vision     - questions about images, drawings, P&IDs, scans or photographs
utility    - trivial rewording, extraction or classification of short text

Answer with JSON only."""


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """What the router needs to decide. Deliberately not a GenerationRequest.

    Routing happens *before* a model exists to send anything to, so it takes the
    intent rather than a built request.
    """

    text: str = ""
    has_images: bool = False
    attachment_count: int = 0
    estimated_tokens: int = 0
    #: Set by agent nodes that already know their own intent.
    node_intent: str | None = None
    #: Explicit override from an operator or admin request.
    lane_hint: str | None = None
    model_hint: str | None = None
    needs_tools: bool = False
    needs_json: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """The routing outcome, persisted and streamed to the trace timeline."""

    lane: Lane
    model: ModelInfo
    #: Which stage settled it: 0 deterministic, 1 lexical, 2 classifier.
    stage_decided: int
    confidence: float
    reason: str
    decide_latency_ms: float
    resident: bool
    swap_cost_s: float
    alternatives: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    features_digest: str = ""
    #: The prompt is larger than any available context window; the caller must
    #: map-reduce rather than send it whole.
    requires_chunking: bool = False

    def to_event(self) -> dict[str, Any]:
        """The shape the frontend's RouteDecisionCard renders."""
        return {
            "lane": self.lane.value,
            "model": self.model.logical_name,
            "physical_model": self.model.physical_id,
            "provider": self.model.provider,
            "stage": self.stage_decided,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "decide_ms": round(self.decide_latency_ms, 2),
            "resident": self.resident,
            "swap_cost_s": round(self.swap_cost_s, 2),
            "alternatives": self.alternatives,
            "requires_chunking": self.requires_chunking,
        }


class ModelRouter:
    """Chooses a lane, then a model within it."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        config_path: Path,
        residency: ResidencyView | None = None,
        profile: str | None = None,
    ) -> None:
        self.registry = registry
        self.residency = residency
        self.profile_name = profile or registry.default_profile
        self._config = self._load_config(config_path)
        self._stage0 = self._config.get("stage0", {})
        self._stage1 = self._config.get("stage1", {})
        self._stage2 = self._config.get("stage2", {})
        selection = self._config.get("selection", {})

        profile = registry.profile(self.profile_name)
        breaker_cfg = selection.get("circuit_breaker", {})
        self.policy = SelectionPolicy(
            prefer=str(profile.get("prefer", "loaded_then_quality")),
            residency_bonus_per_second=float(
                selection.get("residency_bonus_per_second_saved", 0.15)
            ),
            breaker=CircuitBreaker(
                failure_threshold=int(breaker_cfg.get("failure_threshold", 3)),
                window_s=float(breaker_cfg.get("window_s", 60)),
                cooldown_s=float(breaker_cfg.get("cooldown_s", 120)),
            ),
        )

        #: Prompt-hash -> lane. Bounded LRU so a long-running process cannot
        #: grow it without limit.
        self._cache: OrderedDict[str, tuple[Lane, float, str]] = OrderedDict()
        self._cache_max = int(self._stage2.get("cache_size", 2048))

        self.stats: dict[str, Any] = {
            "decisions": 0,
            "by_stage": {0: 0, 1: 0, 2: 0},
            "by_lane": {},
            "cache_hits": 0,
            "latencies_ms": [],
        }

    @staticmethod
    def _load_config(path: Path) -> dict[str, Any]:
        if not path.is_file():
            log.warning("router_config_missing", path=str(path))
            return {}
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.error("router_config_invalid", path=str(path), error=str(exc))
            return {}

    # ------------------------------------------------------------------ route
    async def route(self, request: RouteRequest) -> RouteDecision:
        """Decide the lane and model for one request."""
        started = time.perf_counter()
        features = extract(
            request.text,
            has_images=request.has_images,
            estimated_tokens=request.estimated_tokens,
            attachment_count=request.attachment_count,
        )

        lane, stage, confidence, reason = self._stage0_decide(request, features)
        if lane is None:
            lane, confidence, reason = self._stage1_decide(features)
            stage = 1
            if lane is None:
                lane, confidence, reason = await self._stage2_decide(request.text)
                stage = 2

        decision = self._select_model(
            lane=lane,
            features=features,
            request=request,
            stage=stage,
            confidence=confidence,
            reason=reason,
            started=started,
        )
        self._record(decision)
        return decision

    # ---------------------------------------------------------------- stage 0
    def _stage0_decide(
        self, request: RouteRequest, features: PromptFeatures
    ) -> tuple[Lane | None, int, float, str]:
        """Deterministic rules. Nothing here consults a model."""
        if request.lane_hint:
            try:
                return Lane(request.lane_hint), 0, 1.0, "explicit lane hint"
            except ValueError:
                log.warning("invalid_lane_hint", hint=request.lane_hint)

        # An image in the request forces the vision lane, unconditionally.
        # Letting a classifier weigh in here would eventually route a picture to
        # a text model, which answers confidently about something it never saw.
        if request.has_images and self._stage0.get("image_attachment_forces_vision", True):
            return Lane.VISION, 0, 1.0, "request contains an image attachment"

        if request.node_intent:
            mapping = self._stage0.get("node_intent_map") or {}
            if mapped := mapping.get(request.node_intent):
                try:
                    return Lane(mapped), 0, 1.0, f"agent node intent '{request.node_intent}'"
                except ValueError:
                    log.warning("invalid_node_intent_lane", intent=request.node_intent)

        threshold = int(self._stage0.get("long_ctx_token_threshold", 12000))
        if features.estimated_tokens > threshold:
            return (
                Lane.LONG_CTX,
                0,
                1.0,
                f"prompt is ~{features.estimated_tokens} tokens (> {threshold})",
            )

        return None, 0, 0.0, ""

    # ---------------------------------------------------------------- stage 1
    def _stage1_decide(self, features: PromptFeatures) -> tuple[Lane | None, float, str]:
        """Weighted lexical scoring. Decides only when the winner is clear."""
        weights = self._stage1.get("weights") or {}
        margin_threshold = float(self._stage1.get("margin_threshold", 0.25))
        scores: dict[Lane, float] = {}

        def weight(lane: str, key: str, default: float = 0.0) -> float:
            return float((weights.get(lane) or {}).get(key, default))

        scores[Lane.CODING] = (
            weight("coding", "code_fence") * features.has_code_fence
            + weight("coding", "code_verb") * min(features.code_verb_hits, 3)
            + weight("coding", "file_extension_mention") * min(features.file_extension_hits, 2)
            + weight("coding", "library_mention") * min(features.library_hits, 2)
            + weight("coding", "artifact_code_request") * features.artifact_code_request
        )
        scores[Lane.REASONING] = (
            weight("reasoning", "default_bias", 1.0)
            + weight("reasoning", "comparison_verb") * min(features.comparison_verb_hits, 3)
            + weight("reasoning", "multi_clause") * features.is_multi_clause
            + weight("reasoning", "question_length_gt_40w") * features.is_multi_clause
        )
        scores[Lane.LONG_CTX] = (
            weight("long_ctx", "summarize_whole_doc") * features.summarise_document
            + weight("long_ctx", "multi_document_mention") * features.multi_document
        )
        scores[Lane.VISION] = (
            weight("vision", "drawing_noun") * min(features.drawing_noun_hits, 2)
            + weight("vision", "spatial_verb") * min(features.spatial_verb_hits, 2)
        )
        scores[Lane.UTILITY] = weight("utility", "very_short_query") * features.is_very_short

        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        (top_lane, top_score), (_, runner_up) = ranked[0], ranked[1]

        # Vision needs an actual image. Words like "drawing" in a question about
        # a document the user did not attach must not select a VLM with nothing
        # to look at — stage 0 already handled the case where an image exists.
        if top_lane is Lane.VISION and not features.has_images:
            scores[Lane.VISION] = 0.0
            ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
            (top_lane, top_score), (_, runner_up) = ranked[0], ranked[1]

        if top_score <= 0:
            return None, 0.0, "no lexical signal"
        if top_score - runner_up < margin_threshold:
            return None, 0.0, f"ambiguous: {top_lane.value} {top_score:.2f} vs {runner_up:.2f}"

        confidence = min(1.0, (top_score - runner_up) / max(top_score, 1.0))
        return top_lane, confidence, f"lexical score {top_score:.2f} (margin {top_score - runner_up:.2f})"

    # ---------------------------------------------------------------- stage 2
    async def _stage2_decide(self, text: str) -> tuple[Lane, float, str]:
        """Ask the small pinned model, and remember the answer forever."""
        fallback = self._stage2.get("fallback_lane", "reasoning")
        key = digest(text.strip().lower())

        if cached := self._cache.get(key):
            self._cache.move_to_end(key)
            self.stats["cache_hits"] += 1
            lane, confidence, reason = cached
            return lane, confidence, f"{reason} (cached)"

        model_name = str(self._stage2.get("model", "utility.small"))
        try:
            info = self.registry.get_model(model_name)
            provider = self.registry.get_provider(info.provider)
            result = await provider.generate(
                GenerationRequest(
                    model=info.physical_id,
                    messages=[
                        ChatMessage(role="system", content=_CLASSIFIER_PROMPT),
                        ChatMessage(role="user", content=text[:2000]),
                    ],
                    temperature=float(self._stage2.get("temperature", 0.0)),
                    json_schema=_CLASSIFIER_SCHEMA,
                    max_tokens=200,
                )
            )
            import json

            parsed = json.loads(result.text)
            lane = Lane(parsed["lane"])
            confidence = float(parsed.get("confidence", 0.5))
            reason = f"classifier: {str(parsed.get('reason', ''))[:120]}"
        except Exception as exc:  # noqa: BLE001
            # A classifier failure must never block a user request; the default
            # lane handles anything, just not always optimally.
            log.warning("router_stage2_failed", error=str(exc))
            return Lane(fallback), 0.3, f"classifier unavailable, defaulted to {fallback}"

        self._cache[key] = (lane, confidence, reason)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return lane, confidence, reason

    # ------------------------------------------------------------- selection
    def _select_model(
        self,
        *,
        lane: Lane,
        features: PromptFeatures,
        request: RouteRequest,
        stage: int,
        confidence: float,
        reason: str,
        started: float,
    ) -> RouteDecision:
        if request.model_hint:
            info = self.registry.get_model(request.model_hint)
            return RouteDecision(
                lane=lane,
                model=info,
                stage_decided=0,
                confidence=1.0,
                reason="explicit model hint",
                decide_latency_ms=(time.perf_counter() - started) * 1000,
                resident=bool(self.residency and self.residency.is_resident(info.logical_name)),
                swap_cost_s=self.residency.swap_cost_s(info.logical_name) if self.residency else 0.0,
                features_digest=features.digest(),
            )

        requirements = SelectionRequirements.for_lane(
            lane,
            estimated_tokens=features.estimated_tokens,
            needs_tools=request.needs_tools,
            needs_json=request.needs_json,
        )
        selection = self.policy.select(
            self.registry.lane_candidates(lane),
            requirements,
            residency=self.residency,
        )
        return RouteDecision(
            lane=lane,
            model=selection.model,
            stage_decided=stage,
            confidence=confidence,
            reason=reason,
            decide_latency_ms=(time.perf_counter() - started) * 1000,
            resident=selection.resident,
            swap_cost_s=selection.swap_cost_s,
            alternatives=selection.alternatives,
            rejected=selection.rejected,
            features_digest=features.digest(),
            requires_chunking=selection.requires_chunking,
        )

    def fallback_after_failure(
        self, decision: RouteDecision, attempted: frozenset[str]
    ) -> RouteDecision | None:
        """The next candidate in the lane after a model fails.

        Returns ``None`` when the lane is exhausted, which the caller surfaces as
        an honest failure rather than silently degrading to a different lane.
        """
        self.policy.breaker.record_failure(decision.model.logical_name)
        try:
            selection = self.policy.select(
                self.registry.lane_candidates(decision.lane),
                SelectionRequirements.for_lane(decision.lane),
                residency=self.residency,
                exclude=attempted | {decision.model.logical_name},
            )
        except Exception:  # noqa: BLE001 - lane exhausted
            return None
        return RouteDecision(
            lane=decision.lane,
            model=selection.model,
            stage_decided=decision.stage_decided,
            confidence=decision.confidence * 0.8,
            reason=f"fallback after {decision.model.logical_name} failed",
            decide_latency_ms=0.0,
            resident=selection.resident,
            swap_cost_s=selection.swap_cost_s,
            alternatives=selection.alternatives,
            features_digest=decision.features_digest,
        )

    def record_success(self, decision: RouteDecision) -> None:
        self.policy.breaker.record_success(decision.model.logical_name)

    # ------------------------------------------------------------------ stats
    def _record(self, decision: RouteDecision) -> None:
        self.stats["decisions"] += 1
        self.stats["by_stage"][decision.stage_decided] += 1
        lane_counts = self.stats["by_lane"]
        lane_counts[decision.lane.value] = lane_counts.get(decision.lane.value, 0) + 1
        latencies: list[float] = self.stats["latencies_ms"]
        latencies.append(decision.decide_latency_ms)
        if len(latencies) > 1000:
            del latencies[:-1000]

    def routing_stats(self) -> dict[str, Any]:
        """Aggregates for the admin dashboard and the router eval suite."""
        latencies = sorted(self.stats["latencies_ms"])
        total = self.stats["decisions"] or 1

        def percentile(fraction: float) -> float:
            if not latencies:
                return 0.0
            index = min(len(latencies) - 1, int(len(latencies) * fraction))
            return round(latencies[index], 3)

        return {
            "decisions": self.stats["decisions"],
            "by_lane": dict(self.stats["by_lane"]),
            "by_stage": {str(k): v for k, v in self.stats["by_stage"].items()},
            "stage0_hit_rate": round(self.stats["by_stage"][0] / total, 3),
            "cache_hits": self.stats["cache_hits"],
            "decide_p50_ms": percentile(0.50),
            "decide_p95_ms": percentile(0.95),
            "circuit_breakers": self.policy.breaker.snapshot(),
        }
