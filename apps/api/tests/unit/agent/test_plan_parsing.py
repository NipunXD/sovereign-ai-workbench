"""Plan parsing.

The case that matters here was found by running the demo: asked to "produce a
Word report", the agent retrieved, wrote prose, and generated nothing. The
planner had in fact planned the document — it labelled the step "synthesize"
and named `artifact.docx` on it — but the execute loop stops at the first
synthesize step, so the tool was dropped without a word.

It also hid a security control. No tool ran, so the approval gate never fired,
and a document tool that never fires looks exactly like a document tool that
is correctly permitted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from workbench.agent.runner import AgentRunner
from workbench.agent.state import StepIntent


class _Tools:
    """Just enough of the tool registry for plan parsing."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def catalogue_for(self, _principal: Any) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self._names]


@pytest.fixture
def runner() -> AgentRunner:
    return AgentRunner.__new__(AgentRunner)  # parsing needs no wiring


@pytest.fixture
def state() -> dict[str, Any]:
    return {"errors": [], "principal": None}


def _plan(
    runner: AgentRunner, state: dict[str, Any], steps: list[dict[str, Any]], tools: list[str]
):
    runner.tools = _Tools(tools)  # type: ignore[assignment]
    return runner._parse_plan(json.dumps({"steps": steps}), state)


def test_a_named_tool_wins_over_a_synthesize_label(runner, state) -> None:
    # Verbatim shape of what the planner produced against the real corpus.
    plan = _plan(
        runner,
        state,
        [
            {"id": "1", "intent": "retrieve", "description": "Find the survey"},
            {
                "id": "2",
                "intent": "synthesize",
                "description": "Compile into a Word document using artifact.docx",
                "tool": "artifact.docx",
            },
        ],
        ["artifact.docx"],
    )
    assert [s.intent for s in plan.steps[:2]] == [StepIntent.RETRIEVE, StepIntent.TOOL]
    assert plan.steps[1].tool == "artifact.docx"


def test_a_synthesize_step_with_no_tool_stays_synthesize(runner, state) -> None:
    # The terminator must keep its meaning, or the loop never ends.
    plan = _plan(
        runner,
        state,
        [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {"id": "2", "intent": "synthesize", "description": "Write the answer"},
        ],
        ["artifact.docx"],
    )
    assert plan.steps[-1].intent is StepIntent.SYNTHESIZE
    assert plan.steps[-1].tool is None


def test_a_retrieve_step_is_never_reinterpreted(runner, state) -> None:
    # Retrieval is not a tool call even when the planner attaches a name.
    plan = _plan(
        runner,
        state,
        [{"id": "1", "intent": "retrieve", "description": "Find it", "tool": "artifact.docx"}],
        ["artifact.docx"],
    )
    assert plan.steps[0].intent is StepIntent.RETRIEVE


def test_a_relabelled_step_still_faces_the_permission_check(runner, state) -> None:
    """Reinterpreting the label must not smuggle a step past the tool filter.

    The check that drops unavailable tools runs on the corrected intent, so a
    step this principal may not perform is still dropped — and reported.
    """
    plan = _plan(
        runner,
        state,
        [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {
                "id": "2",
                "intent": "synthesize",
                "description": "Write a report",
                "tool": "artifact.docx",
            },
        ],
        [],  # the principal has no artifact tools
    )
    assert all(s.tool != "artifact.docx" for s in plan.steps)
    assert any("artifact.docx" in e["error"] for e in state["errors"])


def test_a_plan_always_ends_with_synthesis(runner, state) -> None:
    plan = _plan(
        runner,
        state,
        [{"id": "1", "intent": "synthesize", "description": "Report", "tool": "artifact.docx"}],
        ["artifact.docx"],
    )
    # The only step became a tool step, so a terminator has to be appended or
    # the run would end without writing an answer.
    assert plan.steps[-1].intent is StepIntent.SYNTHESIZE
    assert plan.steps[0].intent is StepIntent.TOOL
