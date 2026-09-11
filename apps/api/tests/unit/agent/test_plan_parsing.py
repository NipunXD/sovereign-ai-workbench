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
from typing import Any, ClassVar

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
    return {
        "errors": [],
        "limitations": [],
        "principal": None,
        "user_input": "",
        "run_id": "run_test",
    }


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


class TestTheArtifactStepIsGuaranteed:
    """A request for a document must plan the tool that produces one.

    The prompt asks for this, and the planner usually complies. The case below
    is one where it did not, taken verbatim from a run: six steps, three of
    them `synthesize` describing the Word report it intended to write, and no
    tool named anywhere. The run answered in prose, the person who asked for a
    file got an essay about one, and because no tool ran the approval gate
    never fired.
    """

    #: The real plan, from the trace.
    OBSERVED: ClassVar[list[dict[str, str]]] = [
        {
            "id": "1",
            "intent": "retrieve",
            "description": "Search for documents related to V-1201 thickness survey findings.",
        },
        {
            "id": "2",
            "intent": "retrieve",
            "description": "Search for any available reports or data on V-1201 thickness measurements.",
        },
        {
            "id": "3",
            "intent": "retrieve",
            "description": "Check if there are existing templates or guidelines for creating a Word report.",
        },
        {
            "id": "4",
            "intent": "synthesize",
            "description": "Compile the retrieved information into a structured Word report format.",
        },
        {
            "id": "5",
            "intent": "synthesize",
            "description": "Format the report with appropriate sections.",
        },
        {
            "id": "6",
            "intent": "synthesize",
            "description": "Ensure the report includes all necessary details.",
        },
    ]

    def test_a_plan_that_forgot_the_tool_gets_it(self, runner, state) -> None:
        state["user_input"] = "Produce a Word report on the V-1201 thickness survey findings."
        plan = _plan(runner, state, self.OBSERVED, ["artifact.docx"])

        tools = [s.tool for s in plan.steps if s.tool]
        assert "artifact.docx" in tools, "the requested document was never planned"

    def test_the_step_lands_before_the_final_synthesis(self, runner, state) -> None:
        state["user_input"] = "Produce a Word report on the V-1201 thickness survey."
        plan = _plan(runner, state, self.OBSERVED, ["artifact.docx"])

        index = next(i for i, s in enumerate(plan.steps) if s.tool == "artifact.docx")
        # Built after the evidence is gathered...
        assert any(s.intent is StepIntent.RETRIEVE for s in plan.steps[:index])
        # ...and before the answer that refers to it.
        assert plan.steps[-1].intent is StepIntent.SYNTHESIZE
        assert index < len(plan.steps) - 1

    def test_a_plan_that_already_has_the_tool_is_untouched(self, runner, state) -> None:
        state["user_input"] = "Produce a Word report on V-1201."
        steps = [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {"id": "2", "intent": "tool", "description": "Write it", "tool": "artifact.docx"},
        ]
        plan = _plan(runner, state, steps, ["artifact.docx"])
        assert [s.tool for s in plan.steps if s.tool] == ["artifact.docx"]

    def test_a_mislabelled_artifact_step_counts_as_present(self, runner, state) -> None:
        # The other repair already turned this into a tool step; the guarantee
        # must not then add a second one.
        state["user_input"] = "Produce a Word report on V-1201."
        steps = [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {"id": "2", "intent": "synthesize", "description": "Write it", "tool": "artifact.docx"},
        ]
        plan = _plan(runner, state, steps, ["artifact.docx"])
        assert [s.tool for s in plan.steps if s.tool] == ["artifact.docx"]

    def test_a_question_gets_no_artifact_step(self, runner, state) -> None:
        state["user_input"] = "What is the depressurisation rate limit for V-1201?"
        steps = [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {"id": "2", "intent": "synthesize", "description": "Answer"},
        ]
        plan = _plan(runner, state, steps, ["artifact.docx"])
        assert all(s.tool is None for s in plan.steps)

    def test_nothing_is_added_when_the_user_cannot_generate_documents(self, runner, state) -> None:
        """The guarantee must not plan a step the permission check would drop.

        Adding it anyway would replace a clear "you cannot do this" with a run
        that quietly does less than it said.
        """
        state["user_input"] = "Produce a Word report on V-1201."
        steps = [
            {"id": "1", "intent": "retrieve", "description": "Find it"},
            {"id": "2", "intent": "synthesize", "description": "Answer"},
        ]
        plan = _plan(runner, state, steps, [])  # no artifact tools offered
        assert all(s.tool is None for s in plan.steps)

    def test_the_requested_format_is_the_one_planned(self, runner, state) -> None:
        state["user_input"] = "Make me a briefing deck on the 2029 inspection."
        steps = [{"id": "1", "intent": "retrieve", "description": "Find it"}]
        plan = _plan(runner, state, steps, ["artifact.docx", "artifact.pptx", "artifact.xlsx"])
        assert [s.tool for s in plan.steps if s.tool] == ["artifact.pptx"]


class TestAnUnpermittedRequestIsExplained:
    """Asking for a file you may not generate must be answered, not ignored.

    The approver role holds `artifact:approve` and deliberately not
    `artifact:generate`, so that whoever signs a document off is not whoever
    produced it. A request from that account is therefore correctly refused —
    but the run used to answer in prose with nothing said, leaving the person
    to infer from the file's absence that something had broken.
    """

    def test_the_run_records_why_it_could_not_generate(self, runner, state) -> None:
        state["user_input"] = "Produce a Word report on the V-1201 thickness survey."
        state["principal"] = SimpleNamespace(username="approver")
        _plan(runner, state, [{"id": "1", "intent": "retrieve", "description": "Find it"}], [])

        assert len(state["limitations"]) == 1
        note = state["limitations"][0]
        assert note["kind"] == "artifact_not_permitted"
        assert note["tool"] == "artifact.docx"
        # Names the format, the missing permission, and the way forward.
        assert "Word document" in note["message"]
        assert "artifact:generate" in note["message"]
        assert "approver" in note["message"]

    def test_the_format_asked_for_is_the_one_named(self, runner, state) -> None:
        state["user_input"] = "Make me a briefing deck on the 2029 inspection."
        state["principal"] = SimpleNamespace(username="approver")
        _plan(runner, state, [{"id": "1", "intent": "retrieve", "description": "Find it"}], [])

        assert state["limitations"][0]["tool"] == "artifact.pptx"
        assert "PowerPoint deck" in state["limitations"][0]["message"]

    def test_a_permitted_request_records_no_limitation(self, runner, state) -> None:
        state["user_input"] = "Produce a Word report on the V-1201 thickness survey."
        state["principal"] = SimpleNamespace(username="senior")
        plan = _plan(
            runner,
            state,
            [{"id": "1", "intent": "retrieve", "description": "Find it"}],
            ["artifact.docx"],
        )
        assert state["limitations"] == []
        assert any(s.tool == "artifact.docx" for s in plan.steps)

    def test_a_plain_question_records_no_limitation(self, runner, state) -> None:
        state["user_input"] = "What is the depressurisation rate limit for V-1201?"
        state["principal"] = SimpleNamespace(username="approver")
        _plan(runner, state, [{"id": "1", "intent": "retrieve", "description": "Find it"}], [])
        assert state["limitations"] == []


class TestSynthesisComesLast:
    """A tool step behind a synthesize step must still run.

    Taken from a real run. The planner emitted six steps with the artifact
    tool at position four, behind a synthesize at position three. The executor
    stops at the first synthesize — that step *is* the answer — so the tool
    was never reached: "0 tool calls", and a description of the Word document
    where the document should have been. The trace listed the artifact step
    the whole time, which is what made it hard to see.
    """

    OBSERVED: ClassVar[list[dict[str, str]]] = [
        {"id": "1", "intent": "retrieve", "description": "Search for all documents"},
        {"id": "2", "intent": "retrieve", "description": "Collect citations and summaries"},
        {"id": "3", "intent": "synthesize", "description": "Compile into a structured report"},
        {
            "id": "4",
            "intent": "tool",
            "description": "Generate a Word document",
            "tool": "artifact.docx",
        },
        {"id": "5", "intent": "synthesize", "description": "Ensure citations are summarised"},
        {"id": "6", "intent": "synthesize", "description": "Final review"},
    ]

    def test_the_tool_step_is_reachable(self, runner, state) -> None:
        state["user_input"] = "Create a Word document report of the measurements."
        plan = _plan(runner, state, self.OBSERVED, ["artifact.docx"])

        index = next(i for i, s in enumerate(plan.steps) if s.tool == "artifact.docx")
        before = plan.steps[:index]
        # Nothing that ends the run may sit in front of it.
        assert all(s.intent is not StepIntent.SYNTHESIZE for s in before)

    def test_exactly_one_synthesis_and_it_is_last(self, runner, state) -> None:
        state["user_input"] = "Create a Word document report."
        plan = _plan(runner, state, self.OBSERVED, ["artifact.docx"])

        synthesis = [s for s in plan.steps if s.intent is StepIntent.SYNTHESIZE]
        assert len(synthesis) == 1
        assert plan.steps[-1].intent is StepIntent.SYNTHESIZE

    def test_the_retrievals_keep_their_order(self, runner, state) -> None:
        state["user_input"] = "Create a Word document report."
        plan = _plan(runner, state, self.OBSERVED, ["artifact.docx"])

        assert [s.id for s in plan.steps if s.intent is StepIntent.RETRIEVE] == ["1", "2"]

    def test_a_plan_with_no_synthesis_still_gets_one(self, runner, state) -> None:
        plan = _plan(
            runner, state, [{"id": "1", "intent": "retrieve", "description": "Find it"}], []
        )
        assert plan.steps[-1].intent is StepIntent.SYNTHESIZE

    def test_an_ordinary_plan_is_unchanged(self, runner, state) -> None:
        plan = _plan(
            runner,
            state,
            [
                {"id": "1", "intent": "retrieve", "description": "Find it"},
                {"id": "2", "intent": "synthesize", "description": "Answer"},
            ],
            [],
        )
        assert [s.intent for s in plan.steps] == [StepIntent.RETRIEVE, StepIntent.SYNTHESIZE]


class TestOneDocumentPerRequest:
    """A planner asked for a report with sources plans the document twice.

    Each artifact step is separately gated, so the approver is asked to sign
    off the same report again and a second near-identical file lands in the
    store. Observed on a request for "a report ... with citations": two
    artifact.docx steps, two approvals.
    """

    def test_a_repeated_artifact_tool_is_collapsed(self, runner, state) -> None:
        state["user_input"] = "Create a Word document report of the measurements."
        plan = _plan(
            runner,
            state,
            [
                {"id": "1", "intent": "retrieve", "description": "Find it"},
                {
                    "id": "2",
                    "intent": "tool",
                    "description": "Compile the report",
                    "tool": "artifact.docx",
                },
                {
                    "id": "3",
                    "intent": "tool",
                    "description": "Add a provenance page",
                    "tool": "artifact.docx",
                },
            ],
            ["artifact.docx"],
        )
        assert [s.tool for s in plan.steps if s.tool] == ["artifact.docx"]

    def test_different_formats_both_survive(self, runner, state) -> None:
        state["user_input"] = "Create a Word report and a spreadsheet of the readings."
        plan = _plan(
            runner,
            state,
            [
                {"id": "1", "intent": "retrieve", "description": "Find it"},
                {"id": "2", "intent": "tool", "description": "Report", "tool": "artifact.docx"},
                {"id": "3", "intent": "tool", "description": "Workbook", "tool": "artifact.xlsx"},
            ],
            ["artifact.docx", "artifact.xlsx"],
        )
        assert [s.tool for s in plan.steps if s.tool] == ["artifact.docx", "artifact.xlsx"]

    def test_a_non_artifact_tool_may_repeat(self, runner, state) -> None:
        """The same calculation on different figures is ordinary work."""
        plan = _plan(
            runner,
            state,
            [
                {
                    "id": "1",
                    "intent": "tool",
                    "description": "Rate for CML-04",
                    "tool": "calc.engineering",
                },
                {
                    "id": "2",
                    "intent": "tool",
                    "description": "Rate for CML-06",
                    "tool": "calc.engineering",
                },
            ],
            ["calc.engineering"],
        )
        assert [s.tool for s in plan.steps if s.tool] == ["calc.engineering", "calc.engineering"]
