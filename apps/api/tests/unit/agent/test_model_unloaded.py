"""Recovering when the backend unloads a model underneath a run.

LM Studio unloads idle models on its own timer. The residency manager only
learns that at its next reconciliation, and in between its stale entry makes
`acquire()` a no-op — so the next call fails with "Model unloaded" and, before
this, took the whole run down at its first model call on a machine where the
model was one load away.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from workbench.agent.runner import AgentRunner
from workbench.providers.errors import ProviderResponseError


class _Provider:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def generate(self, request: Any) -> Any:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise ProviderResponseError('lmstudio returned HTTP 400: {"error":"Model unloaded."}')
        return SimpleNamespace(text="{}", usage=SimpleNamespace(total_tokens=3))


class _Residency:
    def __init__(self) -> None:
        self.log: list[str] = []

    async def acquire(self, name: str) -> None:
        self.log.append(f"acquire:{name}")

    def forget(self, name: str) -> None:
        self.log.append(f"forget:{name}")


def _runner(provider: _Provider, residency: _Residency | None) -> AgentRunner:
    runner = AgentRunner.__new__(AgentRunner)
    info = SimpleNamespace(physical_id="qwen/qwen3-8b", default_num_ctx=8192)
    runner.registry = SimpleNamespace(  # type: ignore[assignment]
        get_model=lambda _name: info, provider_for=lambda _name: provider
    )
    runner.residency = residency  # type: ignore[assignment]
    return runner


def _state() -> dict[str, Any]:
    return {"budget": SimpleNamespace(tokens_used=0)}


async def test_an_unloaded_model_is_reloaded_and_the_call_retried() -> None:
    provider, residency = _Provider(failures=1), _Residency()
    state = _state()

    result = await _runner(provider, residency)._generate("reasoning.primary", [], state=state)

    assert result.text == "{}"
    assert provider.calls == 2
    # The belief was corrected before the retry, so acquire() actually loads.
    assert residency.log == [
        "acquire:reasoning.primary",
        "forget:reasoning.primary",
        "acquire:reasoning.primary",
    ]
    assert state["budget"].tokens_used == 3


async def test_it_retries_exactly_once() -> None:
    """A second failure is a real one and must surface."""
    provider, residency = _Provider(failures=2), _Residency()

    with pytest.raises(ProviderResponseError):
        await _runner(provider, residency)._generate("reasoning.primary", [], state=_state())
    assert provider.calls == 2


async def test_other_provider_errors_are_not_retried() -> None:
    class _Other(_Provider):
        async def generate(self, request: Any) -> Any:
            self.calls += 1
            raise ProviderResponseError("lmstudio returned HTTP 500: internal error")

    provider, residency = _Other(failures=0), _Residency()
    with pytest.raises(ProviderResponseError):
        await _runner(provider, residency)._generate("reasoning.primary", [], state=_state())
    assert provider.calls == 1
    assert "forget:reasoning.primary" not in residency.log


async def test_without_a_residency_manager_the_error_surfaces_unchanged() -> None:
    provider = _Provider(failures=1)
    with pytest.raises(ProviderResponseError):
        await _runner(provider, None)._generate("reasoning.primary", [], state=_state())
    assert provider.calls == 1
