"""A refused tool call gets one more attempt, with the reason.

The calculator's refusals are written to be corrected: they name the reading
the documents actually record for that year. Left alone the run carried on
without the calculation and the model did the arithmetic in prose instead —
the one outcome a calculation tool exists to prevent. So the arguments are
bound once more with the rejection in front of the binder.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from workbench.agent.runner import AgentRunner
from workbench.agent.state import Budget, PlanStep, StepIntent
from workbench.tools.base import ToolResult

pytestmark = pytest.mark.anyio


class _Tools:
    """Refuses the first set of arguments and accepts anything else."""

    def __init__(self, *, approval: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.approval = approval

    def requires_approval(self, name: str) -> bool:
        return self.approval

    async def dispatch(self, name: str, args: dict[str, Any], ctx: Any) -> ToolResult:
        self.calls.append(dict(args))
        if args.get("thickness") == "13.90 mm":
            result = ToolResult.refuse("the sources record 12.5 mm for 2023, not 13.9 mm")
        else:
            result = ToolResult(ok=True, metrics={"value": 0.55})
        # The registry traces both events around the call; the runner only
        # relays them, so the stub emits them for the trace assertions.
        await ctx.emit("tool_call", {"tool": name, "args": args})
        await ctx.emit("tool_result", {"tool": name, **result.summary()})
        return result


def _runner(tools: _Tools, bound: list[dict[str, Any]]) -> AgentRunner:
    runner = AgentRunner.__new__(AgentRunner)
    runner.tools = tools  # type: ignore[attr-defined]
    # No gate is wired here; what the approval test checks is that a tool
    # marked as needing approval is not dispatched a second time.
    runner.approval_gate = None  # type: ignore[attr-defined]

    async def bind(state: Any, step: Any, correction: str = "") -> tuple[dict[str, Any], None]:
        step.binding_corrections = [*getattr(step, "binding_corrections", []), correction]
        return bound.pop(0), None

    runner._bind_args = bind  # type: ignore[assignment]
    runner._run_context = staticmethod(lambda state: {})  # type: ignore[assignment]
    return runner


def _state() -> dict[str, Any]:
    return {
        "principal": None,
        "run_id": "run_1",
        "user_input": "compute the corrosion rate for CML-04",
        "budget": Budget(),
        "tool_results": [],
        "scratchpad": [],
        "artifacts": [],
        "errors": [],
        "evidence": [],
        "route_decisions": [],
    }


async def _drain(runner: AgentRunner, state: dict[str, Any], step: PlanStep) -> list[Any]:
    return [event async for event in runner._act(state, step)]


def _step() -> PlanStep:
    return PlanStep(
        id="s1", intent=StepIntent.TOOL, description="calculate", tool="calc.engineering"
    )


class TestRetry:
    async def test_a_refusal_is_retried_with_corrected_arguments(self) -> None:
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        state = _state()
        await _drain(runner, state, _step())

        assert [call["thickness"] for call in tools.calls] == ["13.90 mm", "12.50 mm"]
        # The run keeps the second result, so the calculation is in the answer.
        assert state["tool_results"][-1]["ok"] is True

    async def test_the_rejection_reaches_the_binder(self) -> None:
        """A retry that does not say what was wrong just repeats the mistake."""
        tools = _Tools()
        step = _step()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        await _drain(runner, _state(), step)

        corrections = getattr(step, "binding_corrections", [])
        assert corrections[0] == ""
        assert "12.5 mm" in corrections[1]

    async def test_it_retries_only_once(self) -> None:
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "13.90 mm"}])
        await _drain(runner, _state(), _step())
        assert len(tools.calls) <= 2

    async def test_a_tool_behind_the_approval_gate_is_not_retried(self) -> None:
        """The first call has already been through a person; a second would ask
        them again for a decision they have made."""
        tools = _Tools(approval=True)
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        # The approval path is not exercised here — dispatch is called directly —
        # so what is under test is only that no second dispatch happens.
        await _drain(runner, _state(), _step())
        assert len(tools.calls) == 1

    async def test_a_long_run_still_retries(self) -> None:
        """The wall clock is advisory here: a local-model run routinely passes
        300s and carries on to a good answer. Gating the retry on budget
        exhaustion meant it never fired — a measured run reached the refusal at
        707s and skipped it. Tool-call headroom is the limit that binds."""
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        state = _state()
        state["budget"].started_at = time.monotonic() - 900
        assert state["budget"].exhausted
        await _drain(runner, state, _step())
        assert len(tools.calls) == 2

    async def test_no_retry_without_a_tool_call_left(self) -> None:
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        state = _state()
        state["budget"].tool_calls_used = state["budget"].max_tool_calls - 1
        await _drain(runner, state, _step())
        assert len(tools.calls) == 1

    async def test_a_corrected_step_shows_one_call(self) -> None:
        """The rejected attempt never ran and never reached the answer, so it
        is not drawn as a refused call beside the one that did. A discarded
        attempt on screen reads as a step that went wrong."""
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "12.50 mm"}])
        events = await _drain(runner, _state(), _step())

        results = [e for e in events if e.name == "tool_result"]
        assert len(results) == 1
        assert results[0].data["ok"] is True
        assert results[0].data["retried"] is True
        assert not any(e.data.get("refused") for e in results)

        calls = [e for e in events if e.name == "tool_call"]
        assert len(calls) == 1
        assert calls[0].data["args"] == {"thickness": "12.50 mm"}

    async def test_a_refusal_that_could_not_be_corrected_is_still_shown(self) -> None:
        """Hiding it would leave a silently missing calculation, which is
        worse than an honest refusal."""
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "13.90 mm"}, {"thickness": "13.90 mm"}])
        events = await _drain(runner, _state(), _step())

        results = [e for e in events if e.name == "tool_result"]
        assert len(results) == 1
        assert results[0].data["refused"] is True
        assert "retried" not in results[0].data

    async def test_nothing_is_marked_when_no_retry_happened(self) -> None:
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "12.50 mm"}])
        events = await _drain(runner, _state(), _step())
        results = [e for e in events if e.name == "tool_result"]
        assert all("retried" not in e.data for e in results)

    async def test_a_successful_call_is_not_retried(self) -> None:
        tools = _Tools()
        runner = _runner(tools, [{"thickness": "12.50 mm"}])
        await _drain(runner, _state(), _step())
        assert len(tools.calls) == 1


class _Spec:
    name = "calc.engineering"
    description = "Run an engineering calculation."

    class input_model(BaseModel):  # noqa: N801 — mirrors the ToolSpec attribute
        thickness: str


class _SpecTools(_Tools):
    def spec(self, name: str) -> _Spec:
        return _Spec()


class TestBinding:
    """The retry only means something if binding actually runs again."""

    @staticmethod
    def _runner(generated: str) -> tuple[AgentRunner, list[str]]:
        runner = AgentRunner.__new__(AgentRunner)
        runner.tools = _SpecTools()  # type: ignore[attr-defined]
        runner.arg_binding_timeout_s = 5.0  # type: ignore[attr-defined]
        prompts: list[str] = []

        class _Router:
            async def route(self, request: Any) -> Any:
                return SimpleNamespace(model=SimpleNamespace(logical_name="reasoning.primary"))

        runner.router = _Router()  # type: ignore[attr-defined]

        async def generate(model: str, messages: Any, **kwargs: Any) -> Any:
            prompts.append(messages[-1].content)
            return SimpleNamespace(text=generated, finish_reason="stop")

        runner._generate = generate  # type: ignore[assignment]
        return runner, prompts

    async def test_valid_arguments_are_kept_when_nothing_was_rejected(self) -> None:
        runner, prompts = self._runner('{"thickness": "12.50 mm"}')
        step = _step()
        step.args = {"thickness": "13.90 mm"}
        args, error = await runner._bind_args(_state(), step)

        assert (args, error) == ({"thickness": "13.90 mm"}, None)
        assert prompts == []  # no model call: the shortcut is the point

    async def test_a_correction_bypasses_the_shortcut(self) -> None:
        """A refusal is semantic — the rejected arguments still fit the schema.
        Keeping them because they validate is how the retry silently became a
        no-op in two measured runs."""
        runner, prompts = self._runner('{"thickness": "12.50 mm"}')
        step = _step()
        step.args = {"thickness": "13.90 mm"}
        args, error = await runner._bind_args(
            _state(), step, correction="the sources record 12.5 mm for 2023"
        )

        assert error is None
        assert args == {"thickness": "12.50 mm"}
        assert "12.5 mm for 2023" in prompts[0]
