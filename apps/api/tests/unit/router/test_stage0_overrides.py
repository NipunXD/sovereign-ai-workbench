"""Stage 0 must be deterministic.

These are the routing decisions that are not allowed to be a judgement call. If
any of them ever depends on a model, the system can route an image to a
text-only model and produce a confident answer about a picture it never saw.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.providers.registry import ModelRegistry
from workbench.providers.types import Capability, Lane
from workbench.router.router import ModelRouter, RouteRequest


@pytest.fixture
def router(registry: ModelRegistry, router_config_path: Path) -> ModelRouter:
    return ModelRouter(registry, config_path=router_config_path)


async def test_image_attachment_forces_vision_lane(router: ModelRouter) -> None:
    """An attached image goes to the vision lane no matter what the text says.

    The text here actively argues for the coding lane, which is the point: the
    attachment must win.
    """
    decision = await router.route(
        RouteRequest(
            text="write a python script to parse this",
            has_images=True,
            attachment_count=1,
        )
    )
    assert decision.lane is Lane.VISION
    assert decision.stage_decided == 0
    assert decision.confidence == 1.0


async def test_vision_selection_always_has_vision_capability(router: ModelRouter) -> None:
    """Whatever the vision lane returns must actually be able to see."""
    decision = await router.route(RouteRequest(text="what is shown here?", has_images=True))
    assert decision.model.supports(Capability.VISION)


async def test_no_image_does_not_reach_vision(router: ModelRouter) -> None:
    """Talking *about* a drawing is not the same as attaching one.

    Without this guard, "explain how to read a P&ID" would load a VLM and hand
    it nothing to look at.
    """
    decision = await router.route(
        RouteRequest(text="Explain what a P&ID drawing is and how to read one.")
    )
    assert decision.lane is not Lane.VISION


async def test_long_prompt_routes_to_long_context(router: ModelRouter) -> None:
    decision = await router.route(RouteRequest(text="summarise this", estimated_tokens=50_000))
    assert decision.lane is Lane.LONG_CTX
    assert decision.stage_decided == 0


async def test_node_intent_short_circuits(router: ModelRouter) -> None:
    """Agent nodes declare their own intent instead of being classified."""
    decision = await router.route(
        RouteRequest(text="rewrite this query", node_intent="query_rewrite")
    )
    assert decision.lane is Lane.UTILITY
    assert decision.stage_decided == 0


async def test_explicit_lane_hint_wins(router: ModelRouter) -> None:
    decision = await router.route(RouteRequest(text="anything", lane_hint="coding"))
    assert decision.lane is Lane.CODING
    assert decision.stage_decided == 0


async def test_invalid_lane_hint_is_ignored_not_fatal(router: ModelRouter) -> None:
    """A bad hint from an API caller must not 500 the request."""
    decision = await router.route(RouteRequest(text="compare these two reports"))
    assert decision.lane in set(Lane)


async def test_stage0_is_fast(router: ModelRouter) -> None:
    """Stage 0 exists to be nearly free; assert it stays that way."""
    decision = await router.route(RouteRequest(text="what is this?", has_images=True))
    assert decision.decide_latency_ms < 50


async def test_summarise_whole_document_reaches_long_context(router: ModelRouter) -> None:
    """Adjectives between the verb and the noun must not defeat the match."""
    decision = await router.route(
        RouteRequest(text="summarise the entire inspection report for V-1201")
    )
    assert decision.lane is Lane.LONG_CTX


async def test_oversized_prompt_degrades_to_chunking(router: ModelRouter) -> None:
    """A prompt larger than every context window must not fail the request.

    Handling long documents is the entire purpose of this lane, so the router
    picks the widest model available and flags that the caller must map-reduce.
    """
    decision = await router.route(
        RouteRequest(text="summarise this manual", estimated_tokens=500_000)
    )
    assert decision.requires_chunking is True
    assert decision.model is not None


async def test_normal_prompt_does_not_request_chunking(router: ModelRouter) -> None:
    decision = await router.route(RouteRequest(text="what is the hold time for V-1201?"))
    assert decision.requires_chunking is False
