"""A backend that starts after the service must be found without a restart.

Measured: the API was launched before LM Studio. The boot probe recorded that
LM Studio served nothing, the reasoning lane collapsed to the 2B last-resort
model on Ollama, and it stayed that way through every later request — with LM
Studio up, healthy, and holding four loaded models. Nothing failed. The answers
were just worse, and nothing on screen said why.
"""

from __future__ import annotations

import pytest

from workbench.providers.registry import ModelRegistry

pytestmark = pytest.mark.anyio


class _Provider:
    """Serves nothing until it is 'started'."""

    def __init__(self, served: list[str]) -> None:
        self.served = served
        self.calls = 0

    async def list_models(self) -> list[str]:
        self.calls += 1
        return list(self.served)


def _registry(lmstudio: _Provider, ollama: _Provider) -> ModelRegistry:
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._providers = {"lmstudio": lmstudio, "ollama": ollama}  # type: ignore[attr-defined]
    registry._available = None  # type: ignore[attr-defined]
    registry._probed_at = 0.0  # type: ignore[attr-defined]
    registry._models = {}  # type: ignore[attr-defined]
    registry._lanes = {"reasoning": ["reasoning.primary", "reasoning.alt"]}  # type: ignore[attr-defined]
    return registry


def _with_models(registry: ModelRegistry) -> ModelRegistry:
    from workbench.providers.types import Capability, ModelInfo

    def info(name: str, provider: str, physical: str) -> ModelInfo:
        return ModelInfo(
            logical_name=name,
            provider=provider,
            physical_id=physical,
            capabilities=frozenset({Capability.CHAT}),
            context_window=32768,
            approx_ram_gb=4.0,
        )

    registry._models = {  # type: ignore[attr-defined]
        "reasoning.primary": info("reasoning.primary", "lmstudio", "qwen/qwen3-8b"),
        "reasoning.alt": info("reasoning.alt", "ollama", "qwen3.5:2b"),
    }
    return registry


class TestStale:
    async def test_a_missing_model_makes_the_picture_stale(self) -> None:
        registry = _with_models(_registry(_Provider([]), _Provider(["qwen3.5:2b"])))
        await registry.probe_availability()
        assert registry.stale(ttl_s=0.0) is True

    async def test_a_complete_picture_is_never_stale(self) -> None:
        """Nothing is missing, so there is nothing to go back and ask about."""
        registry = _with_models(_registry(_Provider(["qwen/qwen3-8b"]), _Provider(["qwen3.5:2b"])))
        await registry.probe_availability()
        assert registry.stale(ttl_s=0.0) is False

    async def test_it_is_not_stale_before_the_ttl(self) -> None:
        """Otherwise every request pays for a model listing."""
        registry = _with_models(_registry(_Provider([]), _Provider(["qwen3.5:2b"])))
        await registry.probe_availability()
        assert registry.stale(ttl_s=60.0) is False

    async def test_an_unprobed_registry_is_not_stale(self) -> None:
        registry = _with_models(_registry(_Provider([]), _Provider([])))
        assert registry.stale(ttl_s=0.0) is False


class TestRecovery:
    async def test_the_lane_comes_back_when_the_backend_does(self) -> None:
        lmstudio = _Provider([])
        registry = _with_models(_registry(lmstudio, _Provider(["qwen3.5:2b"])))

        await registry.probe_availability()
        assert [m.logical_name for m in registry.lane_candidates("reasoning")] == ["reasoning.alt"]

        lmstudio.served = ["qwen/qwen3-8b"]  # LM Studio is started
        await registry.probe_availability()
        assert [m.logical_name for m in registry.lane_candidates("reasoning")] == [
            "reasoning.primary",
            "reasoning.alt",
        ]
