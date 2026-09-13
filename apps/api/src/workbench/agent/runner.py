"""The agent loop.

Implemented directly rather than through LangGraph's builder. The graph here is
small and its control flow is the interesting part of the system — plan,
retrieve, decide whether that was enough, act, synthesise, criticise, and loop
back at most twice. Expressing it as ordinary async Python keeps that flow
readable, keeps the trace emission next to the step it describes, and avoids a
framework upgrade changing the semantics of an approval gate.

Every step emits a trace event as it happens. The live SSE stream is a view of
that; ``agent_steps`` in the database is the durable record.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from workbench.agent import numerics
from workbench.agent.artifact_intent import FORMAT_NAMES, detect_format, requested_artifact
from workbench.agent.deferred import execute_approved, to_storable
from workbench.agent.prompts import (
    PLANNER_PROMPT,
    QUERY_REWRITE_PROMPT,
    SUFFICIENCY_PROMPT,
    SYNTHESIS_PROMPT,
)
from workbench.agent.refusal import is_refusal
from workbench.agent.state import (
    AgentState,
    Budget,
    Plan,
    PlanStep,
    StepIntent,
    ValidationReport,
    merge_evidence,
)
from workbench.core.event_names import EventName
from workbench.core.ids import prefixed_id
from workbench.core.logging import get_logger
from workbench.core.prose import plain_maths
from workbench.providers.errors import ProviderResponseError
from workbench.providers.types import ChatMessage, GenerationRequest, ImageRef
from workbench.rag.citations import EvidenceItem, build_evidence_prompt, resolve_markers
from workbench.router.router import ModelRouter, RouteRequest
from workbench.security.rbac import Principal
from workbench.tools.base import ToolContext
from workbench.tools.registry import ToolRegistry

log = get_logger(__name__)

#: Output allowance for an ordinary structured call — a plan, a judgement.
DEFAULT_MAX_TOKENS = 1024

#: Output allowance for generating a tool's arguments.
#:
#: Far larger than the default because a document *is* the arguments: a report
#: spec carries its title, every section heading, the prose of each section,
#: bullet lists and tables, and all of it has to arrive in one JSON object. The
#: previous 1024 was inherited from the planner, where it is ample, and it cut
#: the report off mid-string. On a thinking model the allowance is spent on
#: reasoning first, so nearly six hundred reasoning fragments went by before
#: the JSON began — leaving nothing for the JSON itself.
#:
#: The failure was silent and looked like the wrong thing: "could not produce
#: valid arguments for this tool", as though the model could not follow the
#: schema, when it had followed it perfectly and been cut off mid-sentence.
#:
#: 4096 against a measured 665 for a four-section report on the seed corpus.
#: The margin is for the ceiling, not the average — a survey with a dozen
#: CMLs and a table per section is the case that must not truncate.
ARGUMENT_MAX_TOKENS = 4096

MAX_RETRIEVE_LOOPS = 3
MAX_VALIDATE_LOOPS = 2

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "intent": {"type": "string", "enum": ["retrieve", "tool", "synthesize"]},
                    "description": {"type": "string"},
                    # A union type such as ["string", "null"] is rejected by
                    # LM Studio's constrained decoder, so an unused tool is the
                    # empty string rather than null.
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["id", "intent", "description"],
            },
        },
    },
    "required": ["steps"],
}

_SUFFICIENCY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sufficient"],
}

_REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence_idx": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
                },
                "required": ["sentence_idx", "verdict"],
            },
        }
    },
    "required": ["verdicts"],
}


@dataclass
class TraceEvent:
    name: str
    data: dict[str, Any]


class Retriever:
    """What the runner needs from retrieval. Implemented by rag.retriever."""

    async def search(
        self, query: str, principal: Principal, *, k: int = 8
    ) -> list[EvidenceItem]:  # pragma: no cover - protocol
        raise NotImplementedError


def _with_history(state: AgentState) -> str:
    """The request, preceded by what was said before it in this conversation.

    A follow-up like "and for CML-06?" is meaningless on its own. The prior
    turns are labelled as history rather than merged into the question, so
    the model knows which part is being asked now — and the evidence rules
    still apply to the answer, not to anything the earlier turns claimed.
    """
    history = state.get("history") or []
    if not history:
        return f"Question: {state['user_input']}"
    lines = [f"- {turn['role']}: {turn['content']}" for turn in history]
    return (
        "Conversation so far (for context only; do not cite it as a source):\n"
        + "\n".join(lines)
        + f"\n\nQuestion: {state['user_input']}"
    )


class AgentRunner:
    """Executes one request and streams its trace."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        registry: Any,
        tools: ToolRegistry,
        retriever: Retriever | None = None,
        residency: Any = None,
        approval_gate: Any = None,
        approval_wait_s: float = 180.0,
        # Raised from 90s with the token allowance above. The two are one
        # setting in practice: a budget large enough to hold a document is
        # also long enough to generate that the old bound started firing,
        # trading a truncated document for no document at all. A measured
        # four-section report takes 46s on the 8B model here; the run's own
        # wall budget (300s) remains the real ceiling.
        arg_binding_timeout_s: float = 240.0,
    ) -> None:
        self.router = router
        self.registry = registry
        self.tools = tools
        self.retriever = retriever
        self.residency = residency
        self.approval_gate = approval_gate
        #: How long a run holds open waiting for a person. Bounded: a request
        #: that waits forever is a queue of half-finished work nobody can see.
        self.approval_wait_s = approval_wait_s
        self.arg_binding_timeout_s = arg_binding_timeout_s

    # ------------------------------------------------------------------ run
    async def run(
        self,
        *,
        user_input: str,
        principal: Principal,
        run_id: str | None = None,
        conversation_id: str = "",
        attachments: list[dict[str, Any]] | None = None,
        budget: Budget | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[TraceEvent]:
        """Execute the loop, yielding trace events as they happen."""
        state: AgentState = {
            "run_id": run_id or prefixed_id("run"),
            "conversation_id": conversation_id,
            "history": list(history or []),
            "principal": principal,
            "user_input": user_input,
            "attachments": attachments or [],
            "evidence": [],
            "scratchpad": [],
            "route_decisions": [],
            "tool_results": [],
            "artifacts": [],
            "errors": [],
            "limitations": [],
            "loops": {"retrieve": 0, "validate": 0},
            "budget": budget or Budget(),
            "status": "running",
        }

        started = time.perf_counter()
        yield TraceEvent(
            EventName.RUN_STARTED,
            {"run_id": state["run_id"], "input": user_input[:500], "actor": principal.username},
        )

        try:
            async for event in self._plan(state):
                yield event
            async for event in self._execute(state):
                yield event
            async for event in self._synthesise(state):
                yield event
        except Exception as exc:
            # A failure must produce an honest message, never a silent stop.
            log.exception("agent_run_failed", run_id=state["run_id"], error=str(exc))
            state["status"] = "failed"
            state["final"] = (
                f"I could not complete this request: {type(exc).__name__}. "
                f"Nothing was generated or changed."
            )
            yield TraceEvent(
                EventName.ERROR,
                {"code": type(exc).__name__, "message": str(exc)[:300], "recoverable": False},
            )

        state.setdefault("final", "")
        yield TraceEvent(
            EventName.RUN_FINISHED,
            {
                "run_id": state["run_id"],
                "status": state.get("status", "succeeded"),
                "wall_ms": int((time.perf_counter() - started) * 1000),
                "budget": state["budget"].as_dict(),
                "citations": len(state.get("citations") or []),
                "evidence_used": len(state.get("evidence") or []),
            },
        )

    # ----------------------------------------------------------------- plan
    async def _plan(self, state: AgentState) -> AsyncIterator[TraceEvent]:
        principal = state["principal"]
        # The catalogue is filtered by permission, so the planner cannot even
        # name a tool this user may not run.
        catalogue = self.tools.catalogue_for(principal)
        catalogue_text = (
            "\n".join(f'- name: "{s.name}"\n  purpose: {s.description}' for s in catalogue)
            or "(no tools available to this user)"
        )

        decision = await self.router.route(
            RouteRequest(
                text=state["user_input"],
                node_intent="plan",
                needs_json=True,
                has_images=bool(state["attachments"]),
            )
        )
        state["route_decisions"].append(decision.to_event())
        yield TraceEvent(EventName.ROUTE_DECISION, {"node": "plan", **decision.to_event()})

        result = await self._generate(
            decision.model.logical_name,
            [
                ChatMessage(role="system", content=f"{PLANNER_PROMPT}\n\nTools:\n{catalogue_text}"),
                ChatMessage(role="user", content=_with_history(state)),
            ],
            json_schema=_PLAN_SCHEMA,
            state=state,
        )

        plan = self._parse_plan(result.text, state)
        state["plan"] = plan
        yield TraceEvent(EventName.PLAN_CREATED, plan.as_dict())

        # A plan that silently lost its calculation step still produces an
        # answer, which is the worst way for this to fail. Say so.
        for problem in state["errors"]:
            if problem.get("node") == "plan":
                yield TraceEvent(
                    EventName.ERROR,
                    {"code": "plan_step_dropped", "message": problem["error"], "recoverable": True},
                )

        # Emitted as well as put in the synthesis prompt. The prompt asks the
        # model to say this and it usually will; the event means the user is
        # told whether or not it does. A refusal the person never sees is the
        # thing being fixed, so it cannot rest on the model's cooperation.
        for limitation in state["limitations"]:
            yield TraceEvent(EventName.LIMITATION, dict(limitation))

    def _parse_plan(self, raw: str, state: AgentState) -> Plan:
        """Turn the planner's JSON into a Plan, tolerating a bad response.

        A planner that emits unusable JSON must not fail the request; the
        fallback — retrieve, then answer — handles most questions anyway.
        """
        fallback = Plan(
            steps=[
                PlanStep(id="s1", intent=StepIntent.RETRIEVE, description="Search the documents"),
                PlanStep(id="s2", intent=StepIntent.SYNTHESIZE, description="Write the answer"),
            ],
            rationale="default plan (planner output unusable)",
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            state["errors"].append({"node": "plan", "error": "planner returned invalid JSON"})
            return fallback

        steps: list[PlanStep] = []
        available = {spec.name for spec in self.tools.catalogue_for(state["principal"])}
        for index, raw_step in enumerate(payload.get("steps") or [], start=1):
            try:
                intent = StepIntent(raw_step.get("intent", "retrieve"))
            except ValueError:
                continue
            tool = (raw_step.get("tool") or "").strip() or None
            if tool in {"null", "none", "None"}:
                tool = None

            # A step that names a tool is a tool step, whatever the model
            # called it. Planners routinely label artifact generation as
            # "synthesize" — it is, in the everyday sense — and the execute
            # loop stops at the first synthesize step, so the tool was
            # silently dropped and the run answered in prose instead. Asked to
            # "produce a Word report", the agent explained the findings and
            # generated nothing, and because no tool ran, the approval gate
            # had nothing to gate: a document tool that never fires looks
            # exactly like a document tool that is correctly permitted.
            #
            # The presence of `tool` is the reliable signal here and the
            # intent label is not, so the label yields to it.
            if tool is not None and intent is not StepIntent.RETRIEVE:
                intent = StepIntent.TOOL

            if intent is StepIntent.TOOL and tool not in available:
                # The planner named a tool this principal cannot use, or one
                # that does not exist. Drop the step rather than fail later.
                state["errors"].append(
                    {
                        "node": "plan",
                        "error": (
                            f"dropped plan step {raw_step.get('id')}: tool '{tool}' is not "
                            f"available to this user (offered: {', '.join(sorted(available)) or 'none'})"
                        ),
                    }
                )
                continue
            steps.append(
                PlanStep(
                    id=str(raw_step.get("id") or f"s{index}"),
                    intent=intent,
                    description=str(raw_step.get("description", "")),
                    tool=tool,
                    args=dict(raw_step.get("args") or {}),
                )
            )

        if not steps:
            return fallback

        steps = self._ensure_requested_artifact(steps, state, available)

        steps = self._synthesis_last(steps)
        return Plan(steps=steps[:6], rationale=str(payload.get("rationale", "")))

    @staticmethod
    def _synthesis_last(steps: list[PlanStep]) -> list[PlanStep]:
        """Put every action before the answer, and leave exactly one answer.

        The executor stops at the first synthesize step, because that step *is*
        the answer and nothing follows it. A planner that emits

            retrieve, retrieve, synthesize, tool:artifact.docx, synthesize

        therefore stranded the tool: the run stopped at step 3, reported "0
        tool calls", and produced a description of the document instead of the
        document. The plan looked right in the trace, which is what made it
        hard to see — the artifact step was there, listed, never reached.

        The prompt already says synthesis is always last. This makes it true
        rather than hoping, and collapses the trailing run of "format the
        report", "review the report" steps that mean nothing to a node whose
        whole job is to write the answer once.
        """
        actions = [s for s in steps if s.intent is not StepIntent.SYNTHESIZE]
        synthesis = [s for s in steps if s.intent is StepIntent.SYNTHESIZE]

        # One document per request. A planner asked for a report with a
        # provenance page reads that as two jobs and plans artifact.docx
        # twice; each one is separately gated, so the approver is asked to
        # sign off the same report a second time and a second near-identical
        # file lands in the store. Other tools may legitimately repeat — the
        # same calculation on different figures is normal — so only the ones
        # that produce a file are collapsed.
        seen_artifact: set[str] = set()
        deduplicated: list[PlanStep] = []
        for step in actions:
            tool = step.tool or ""
            if tool.startswith("artifact."):
                if tool in seen_artifact:
                    continue
                seen_artifact.add(tool)
            deduplicated.append(step)
        actions = deduplicated
        final = (
            synthesis[-1]
            if synthesis
            else PlanStep(id="final", intent=StepIntent.SYNTHESIZE, description="Write the answer")
        )
        return [*actions, final]

    @staticmethod
    def _ensure_requested_artifact(
        steps: list[PlanStep], state: AgentState, available: set[str]
    ) -> list[PlanStep]:
        """Guarantee that a request for a document plans one.

        The prompt asks for this and the planner usually complies. Usually is
        not enough here, because the failure is silent: asked to "produce a
        Word report" the planner emitted three retrieve steps and three
        `synthesize` ones describing the document it intended to write, named
        no tool at all, and the run answered in prose. The person who asked for
        a file got an essay about one, and since no tool ran, the approval gate
        never fired.

        So the request is read directly and the plan repaired. A step is only
        ever *added* — an existing artifact step, whatever the planner called
        it, is left alone.
        """
        asked_for = detect_format(str(state.get("user_input") or ""))
        if asked_for is not None and asked_for not in available:
            # The request was understood and refused, which is not the same as
            # not noticing it. Saying so is the whole point: the run otherwise
            # answers in prose and the person who asked for a file is left to
            # infer from its absence that something went wrong.
            state["limitations"].append(
                {
                    "kind": "artifact_not_permitted",
                    "tool": asked_for,
                    "message": (
                        f"A {FORMAT_NAMES.get(asked_for, 'document')} was requested, but "
                        f"{getattr(state.get('principal'), 'username', 'this account')} "
                        f"does not hold the "
                        f"'artifact:generate' permission, so this run cannot produce "
                        f"files. The findings are answered below instead. Generating "
                        f"the document needs an account with that permission — the "
                        f"engineer and senior engineer roles have it, and the approver "
                        f"role deliberately does not, so that the person who signs a "
                        f"document off is not the person who produced it."
                    ),
                }
            )
            return steps

        wanted = requested_artifact(str(state.get("user_input") or ""), available)
        if wanted is None:
            return steps
        if any(step.tool == wanted for step in steps):
            return steps

        # Placed before the closing synthesis so the answer can refer to the
        # document that now exists, and after everything else so it is built
        # from the evidence the earlier steps gathered.
        insert_at = len(steps)
        while insert_at > 0 and steps[insert_at - 1].intent is StepIntent.SYNTHESIZE:
            insert_at -= 1

        log.info("plan_artifact_step_added", tool=wanted, run_id=state.get("run_id"))
        steps.insert(
            insert_at,
            PlanStep(
                id="artifact",
                intent=StepIntent.TOOL,
                description=(
                    f"Produce the requested document with {wanted}, using the "
                    f"evidence gathered above."
                ),
                tool=wanted,
            ),
        )
        return steps

    # -------------------------------------------------------------- execute
    async def _execute(self, state: AgentState) -> AsyncIterator[TraceEvent]:
        plan: Plan = state["plan"]
        budget: Budget = state["budget"]

        while (step := plan.next_step) is not None:
            if step.intent is StepIntent.SYNTHESIZE:
                return
            if budget.exhausted:
                # Not an error: answer with what is available and say so.
                state["scratchpad"].append({"note": f"stopped early — {budget.reason()}"})
                yield TraceEvent(
                    EventName.VALIDATION,
                    {"budget_exhausted": True, "reason": budget.reason()},
                )
                return

            yield TraceEvent(
                EventName.STEP_STARTED,
                {"step_id": step.id, "intent": step.intent.value, "description": step.description},
            )

            if step.intent is StepIntent.RETRIEVE:
                async for event in self._retrieve(state, step):
                    yield event
            else:
                async for event in self._act(state, step):
                    yield event

            step.done = True
            yield TraceEvent(EventName.STEP_FINISHED, {"step_id": step.id})

    async def _retrieve(self, state: AgentState, step: PlanStep) -> AsyncIterator[TraceEvent]:
        if self.retriever is None:
            state["errors"].append({"node": "retrieve", "error": "no retriever configured"})
            return

        query = str(step.args.get("query") or state["user_input"])
        loops = state["loops"]

        while True:
            found = await self.retriever.search(query, state["principal"], k=8)
            state["evidence"] = merge_evidence(state.get("evidence"), found)
            yield TraceEvent(
                EventName.RETRIEVAL_RESULT,
                {
                    "query": query,
                    "hits": [
                        {
                            "chunk_id": item.chunk_id,
                            # The document id travels with every hit, cited or
                            # not, so the evidence map can open a document the
                            # answer read but did not end up citing.
                            "doc_id": item.doc_id,
                            "doc_title": item.doc_title,
                            "page": item.page_from,
                            "bbox": item.bbox.as_dict(),
                            "section_path": list(item.section_path),
                            "snippet": item.text.strip().replace("\n", " ")[:240],
                            "score": round(item.score, 4),
                            "method": item.retrieval_method,
                            "confidence": round(item.confidence, 3),
                        }
                        for item in found[:8]
                    ],
                    "total_evidence": len(state["evidence"]),
                },
            )

            if loops["retrieve"] >= MAX_RETRIEVE_LOOPS - 1:
                return
            sufficient, missing = await self._sufficient(state)
            if sufficient or not state["evidence"]:
                # With no evidence at all, rewriting the query is unlikely to
                # help; synthesis will produce an honest refusal instead.
                return

            loops["retrieve"] += 1
            rewritten = await self._rewrite(state, missing)
            if not rewritten:
                return
            query = rewritten[0]
            yield TraceEvent(
                EventName.STEP_STARTED,
                {
                    "step_id": f"{step.id}.r{loops['retrieve']}",
                    "intent": "retrieve",
                    "description": f"Retrying with: {query}",
                },
            )

    async def _sufficient(self, state: AgentState) -> tuple[bool, list[str]]:
        decision = await self.router.route(
            RouteRequest(text=state["user_input"], node_intent="sufficiency", needs_json=True)
        )
        evidence_text = build_evidence_prompt(state["evidence"][:8])
        result = await self._generate(
            decision.model.logical_name,
            [
                ChatMessage(role="system", content=SUFFICIENCY_PROMPT),
                ChatMessage(
                    role="user",
                    content=f"Question: {state['user_input']}\n\n{evidence_text}",
                ),
            ],
            json_schema=_SUFFICIENCY_SCHEMA,
            state=state,
        )
        try:
            payload = json.loads(result.text)
            return bool(payload.get("sufficient")), list(payload.get("missing") or [])
        except (json.JSONDecodeError, AttributeError):
            # An unparseable verdict should not trigger another retrieval loop.
            return True, []

    async def _rewrite(self, state: AgentState, missing: list[str]) -> list[str]:
        from workbench.router.features import equipment_tags

        decision = await self.router.route(
            RouteRequest(text=state["user_input"], node_intent="query_rewrite", needs_json=True)
        )
        tags = equipment_tags(state["user_input"])
        hint = f"\nEquipment tags mentioned: {', '.join(tags)}" if tags else ""
        result = await self._generate(
            decision.model.logical_name,
            [
                ChatMessage(role="system", content=QUERY_REWRITE_PROMPT),
                ChatMessage(
                    role="user",
                    content=(
                        f"Question: {state['user_input']}\n"
                        f"Missing: {', '.join(missing) or 'unclear'}{hint}"
                    ),
                ),
            ],
            json_schema=_REWRITE_SCHEMA,
            state=state,
        )
        try:
            return [str(q) for q in json.loads(result.text).get("queries") or []][:3]
        except (json.JSONDecodeError, AttributeError):
            return []

    async def _bind_args(
        self, state: AgentState, step: PlanStep
    ) -> tuple[dict[str, Any], str | None]:
        """Fill in a tool call's arguments from what the run has actually found.

        The planner writes a step before retrieval runs, so it cannot know the
        values — it knows only that a calculation is needed. Binding the
        arguments here, against the evidence gathered so far, is the difference
        between a plan that is executed blindly and an agent that reacts to what
        it found. It is also why a plan-time guess at "12.5 mm" would be a
        hallucinated input to an otherwise trustworthy calculation.
        """
        spec = self.tools.spec(step.tool or "")
        schema = spec.input_model.model_json_schema()

        # If the planner already produced valid arguments, keep them.
        if step.args:
            try:
                spec.input_model.model_validate(step.args)
                return step.args, None
            except Exception as exc:
                # The planner's arguments did not fit the schema, so they are
                # regenerated below. Expected often enough not to warn, but
                # silence here made a schema mismatch look like a slow model.
                log.debug("planner_args_rejected", tool=step.tool, error=str(exc)[:200])

        decision = await self.router.route(
            RouteRequest(text=step.description, node_intent="plan", needs_json=True)
        )
        evidence_text = build_evidence_prompt(state.get("evidence") or [])
        prior = (
            json.dumps(state["scratchpad"], indent=2)[:2000] if state["scratchpad"] else "(none)"
        )

        # Bounded. Constrained decoding against a large schema can stall for
        # minutes on a local model, and a run that silently hangs on argument
        # generation is indistinguishable from one that crashed.
        try:
            result = await asyncio.wait_for(
                self._generate(
                    decision.model.logical_name,
                    [
                        ChatMessage(
                            role="system",
                            content=(
                                f"Produce the arguments for the tool '{spec.name}'.\n"
                                f"{spec.description}\n\n"
                                "Take every value from the sources or from earlier tool "
                                "results. Never invent a number. If a required value is not "
                                'present, return {"_missing": "what is absent"} instead of '
                                "guessing.\n\n"
                                "Cite as you go: after each claim, figure or table cell taken "
                                "from a source, put [n] where n is the bracketed number at the "
                                "start of that <source> block. Two sources is [1][2]. Put the "
                                "marker in the cell or sentence it supports, not once at the "
                                "end of a section. Cite only numbers that appear in the "
                                "sources; a number with no source behind it is removed.\n\n"
                                "Respond with JSON matching this schema only."
                            ),
                        ),
                        ChatMessage(
                            role="user",
                            content=(
                                f"Question: {state['user_input']}\n"
                                f"This step: {step.description}\n\n{evidence_text}\n\n"
                                f"Earlier tool results:\n{prior}"
                            ),
                        ),
                    ],
                    json_schema=schema,
                    state=state,
                    max_tokens=ARGUMENT_MAX_TOKENS,
                ),
                timeout=self.arg_binding_timeout_s,
            )
        except TimeoutError:
            return {}, (
                f"could not produce arguments for '{spec.name}' within "
                f"{self.arg_binding_timeout_s:.0f}s"
            )

        try:
            bound = json.loads(result.text)
        except json.JSONDecodeError as exc:
            # Truncation and malformed output need different words, because
            # they need different fixes and the operator can only act on the
            # difference. The text is logged either way: reporting a parse
            # failure without it makes the next occurrence just as opaque.
            log.warning(
                "tool_arguments_unparseable",
                tool=spec.name,
                finish_reason=result.finish_reason,
                chars=len(result.text),
                error=str(exc),
                text_tail=result.text[-200:],
            )
            if result.finish_reason == "length":
                return {}, (
                    f"the arguments for '{spec.name}' were cut off at the token "
                    f"limit before they were complete"
                )
            return {}, "could not produce valid arguments for this tool"
        if "_missing" in bound:
            return {}, f"required input not found in the sources: {bound['_missing']}"
        return bound, None

    async def _await_approval(
        self, state: AgentState, step: PlanStep
    ) -> AsyncIterator[tuple[TraceEvent | None, bool | None]]:
        """Pause the run until a person decides, or until the wait expires.

        Yields (event, decision). The decision is True for approved, False for
        refused, and None when nobody answered in time — in which case the
        request stays pending and the run reports honestly that it is waiting,
        rather than pretending it finished.
        """
        from workbench.agent.approval import ApprovalRequest

        summary = {
            "tool": step.tool,
            "description": step.description,
            "arguments": self.tools._summarise_args_dict(step.args)
            if hasattr(self.tools, "_summarise_args_dict")
            else step.args,
            "question": state["user_input"][:400],
        }

        approval = await self.approval_gate.request(
            run_id=state["run_id"],
            step_id=step.id,
            principal=state["principal"],
            request=ApprovalRequest(
                kind="tool",
                subject_type="tool",
                subject_id=step.tool or "",
                summary=summary,
                # Enough to carry this out later, without this run. The
                # arguments are already bound — they were needed to ask the
                # question — so an approver deciding tomorrow approves exactly
                # what an approver deciding now would have.
                deferred={
                    # The run that asked, so the document traces back to it
                    # rather than to the approval — without this the
                    # provenance page names the approval id as the run, and
                    # the trail stops one link short of the conversation.
                    "run_id": state["run_id"],
                    "tool": step.tool,
                    "args": step.args,
                    "run_context": to_storable(self._run_context(state)),
                    "conversation_id": state.get("conversation_id"),
                    "requested_by": state["principal"].username,
                },
                reason=f"{step.tool} requires approval before it runs",
            ),
        )

        state["approval"] = {"id": approval.id, "tool": step.tool, "status": "pending"}
        yield (
            TraceEvent(
                EventName.APPROVAL_REQUIRED,
                {
                    "approval_id": approval.id,
                    "kind": "tool",
                    "tool": step.tool,
                    "summary": summary,
                    "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                    "waiting_s": self.approval_wait_s,
                },
            ),
            None,
        )

        import asyncio

        from workbench.db.models import ApprovalStatus

        deadline = time.monotonic() + self.approval_wait_s
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            current = await self.approval_gate.status(approval.id)
            if current is None or current.status == ApprovalStatus.PENDING:
                continue
            approved = current.status == ApprovalStatus.APPROVED
            state["approval"] = {
                "id": approval.id,
                "tool": step.tool,
                "status": current.status,
                "decided_by": current.decided_by,
                "decided_by_username": await self._approver_name(current.decided_by),
                "decided_at": current.decided_at.isoformat() if current.decided_at else None,
            }
            yield (
                TraceEvent(
                    EventName.APPROVAL_REQUIRED,
                    {
                        "approval_id": approval.id,
                        "status": current.status,
                        "approved": approved,
                        "comment": current.comment,
                        # Named, so the chat can say who decided rather than
                        # only that someone did.
                        "decided_by": (state.get("approval") or {}).get("decided_by_username"),
                        "decided_at": (state.get("approval") or {}).get("decided_at"),
                    },
                ),
                approved,
            )
            return

        yield (None, None)

    async def _approver_name(self, user_id: str | None) -> str | None:
        """Resolve an approver's name for the provenance block."""
        if not user_id:
            return None
        try:
            from workbench.db.session import session_scope
            from workbench.security.identity import display_name

            async with session_scope() as session:
                return await display_name(session, user_id)
        except Exception:
            return user_id

    async def _act(self, state: AgentState, step: PlanStep) -> AsyncIterator[TraceEvent]:
        if not step.tool:
            return
        budget: Budget = state["budget"]

        bound_args, binding_error = await self._bind_args(state, step)
        if binding_error:
            state["scratchpad"].append({"tool": step.tool, "ok": False, "error": binding_error})
            yield TraceEvent(
                EventName.TOOL_RESULT,
                {"tool": step.tool, "ok": False, "error": binding_error},
            )
            return
        step.args = bound_args

        # --- the human gate ---
        # Whether approval is needed comes from the tool spec and policy, never
        # from the model. It is not asked, so it cannot talk its way past.
        if self.tools.requires_approval(step.tool) and self.approval_gate is not None:
            decision = None
            async for event, decision in self._await_approval(state, step):  # noqa: B007
                if event is not None:
                    yield event
            if decision is not True:
                note = (
                    "the request was refused by an approver"
                    if decision is False
                    else (
                        "still waiting for an approver — the document will be "
                        "produced when someone approves it, without this run"
                    )
                )
                state["scratchpad"].append({"tool": step.tool, "ok": False, "error": note})
                state["status"] = "awaiting_approval" if decision is None else "running"
                yield TraceEvent(
                    EventName.TOOL_RESULT,
                    {"tool": step.tool, "ok": False, "error": note},
                )
                return

            # Approved. The decision itself may already have carried the action
            # out — whoever gets there first does the work exactly once — so
            # take that result rather than generating a second copy.
            approval_id = (state.get("approval") or {}).get("id")
            if approval_id:
                done = await execute_approved(
                    approval_id, tools=self.tools, principal=state["principal"]
                )
                if done:
                    budget.tool_calls_used += 1
                    step.result_summary = done
                    state["tool_results"].append({"tool": step.tool, **done})
                    for artifact in done.get("artifacts") or []:
                        state["artifacts"].append(artifact)
                        yield TraceEvent(EventName.ARTIFACT_CREATED, artifact)
                    state["scratchpad"].append({"tool": step.tool, **done})
                    yield TraceEvent(EventName.TOOL_RESULT, {"tool": step.tool, **done})
                    return

        ctx = ToolContext(
            principal=state["principal"],
            run_id=state["run_id"],
            step_id=step.id,
            deadline=budget.deadline,
            run_context=self._run_context(state),
        )

        collected: list[TraceEvent] = []

        async def emit(name: str, data: dict[str, Any]) -> None:
            collected.append(TraceEvent(name, data))

        ctx.emit = emit

        try:
            result = await self.tools.dispatch(step.tool, step.args, ctx)
        except Exception as exc:
            state["errors"].append({"node": "act", "tool": step.tool, "error": str(exc)})
            for event in collected:
                yield event
            yield TraceEvent(
                EventName.TOOL_RESULT,
                {"tool": step.tool, "ok": False, "error": str(exc)[:300]},
            )
            return

        budget.tool_calls_used += 1
        for event in collected:
            yield event

        step.result_summary = result.summary()
        state["tool_results"].append({"tool": step.tool, **result.summary()})
        if result.artifacts:
            state["artifacts"].extend(result.artifacts)
            for artifact in result.artifacts:
                yield TraceEvent(EventName.ARTIFACT_CREATED, artifact)
        # Only a compact form enters the scratchpad; the full payload stays out
        # of the context window.
        state["scratchpad"].append(
            {
                "tool": step.tool,
                "ok": result.ok,
                "result": self._compact(result.data),
                "error": result.error,
            }
        )

    @staticmethod
    def _run_context(state: AgentState) -> dict[str, Any]:
        """What the run has established, for the provenance block.

        The evidence actually retrieved, the models actually routed to, and the
        approver if one has already decided — all taken from the run's own
        record rather than asked of the model.
        """
        models: dict[str, str] = {}
        for decision in state.get("route_decisions") or []:
            logical = str(decision.get("model", ""))
            physical = str(decision.get("physical_model", ""))
            if logical:
                models[logical] = physical

        approval = state.get("approval") or {}
        return {
            "models": models,
            # Every retrieved passage becomes a numbered source, so a document
            # the answer leaned on appears even if the model forgot to cite it.
            "citations": [
                item.to_citation(index)
                for index, item in enumerate(state.get("evidence") or [], start=1)
            ],
            "tools": [record.get("tool", "") for record in state.get("tool_results") or []],
            # The full retrieved text, not the truncated snippets a citation
            # carries. A tool that wants to check an argument against the
            # source — "is 13.90 mm really the 2023 reading?" — needs the row
            # the figure sits in, and that is routinely past the snippet cut.
            "source_text": [item.text for item in state.get("evidence") or []],
            "approved_by": approval.get("decided_by_username"),
            "approved_at": approval.get("decided_at"),
        }

    @staticmethod
    def _compact(data: Any) -> Any:
        if data is None:
            return None
        try:
            payload = data.model_dump(mode="json")
        except AttributeError:
            return str(data)[:1000]
        return json.loads(
            json.dumps(payload)[:2000] + ("}" if len(json.dumps(payload)) > 2000 else "")
        )

    # ------------------------------------------------------------ synthesise
    async def _synthesise(self, state: AgentState) -> AsyncIterator[TraceEvent]:
        evidence: list[EvidenceItem] = state.get("evidence") or []
        decision = await self.router.route(
            RouteRequest(
                text=state["user_input"],
                node_intent="synthesize",
                estimated_tokens=sum(len(i.text) for i in evidence) // 4,
                has_images=bool(state["attachments"]),
            )
        )
        state["route_decisions"].append(decision.to_event())
        yield TraceEvent(EventName.ROUTE_DECISION, {"node": "synthesize", **decision.to_event()})

        messages = [
            ChatMessage(role="system", content=SYNTHESIS_PROMPT),
            ChatMessage(role="user", content=self._synthesis_input(state, evidence)),
        ]
        if state["attachments"]:
            messages[-1].images = [
                ImageRef(data=a["data"], mime_type=a.get("mime", "image/png"))
                for a in state["attachments"]
                if a.get("data")
            ]

        provider = self.registry.provider_for(decision.model.logical_name)
        physical = self.registry.get_model(decision.model.logical_name)
        draft_parts: list[str] = []

        async for chunk in provider.stream(
            GenerationRequest(
                model=physical.physical_id,
                messages=messages,
                temperature=0.2,
                num_ctx=physical.default_num_ctx,
                max_tokens=2048,
            )
        ):
            if chunk.reasoning_delta:
                yield TraceEvent(EventName.REASONING, {"text": chunk.reasoning_delta})
            elif chunk.done:
                state["budget"].tokens_used += int(chunk.meta.get("completion_tokens", 0))
            else:
                draft_parts.append(chunk.delta)
                yield TraceEvent(EventName.TOKEN, {"text": chunk.delta})

        draft = "".join(draft_parts)
        state["draft"] = draft

        # LaTeX out before markers are numbered, so the answer, the stored
        # message and anything a report quotes from it all carry the same text.
        resolved = resolve_markers(plain_maths(draft), evidence)
        state["final"] = resolved.text
        state["citations"] = resolved.citations
        for citation in resolved.citations:
            yield TraceEvent(EventName.CITATION, citation.as_dict())

        # The canonical answer. Everything streamed as `token` still carries raw
        # [[cite:...]] markers, because a marker cannot be renumbered until the
        # text around it exists.
        yield TraceEvent(
            EventName.ANSWER,
            {
                "text": resolved.text,
                "citations": [c.as_dict() for c in resolved.citations],
                "unresolved": resolved.unresolved,
            },
        )

        report = await self._validate(state, resolved)
        state["validation"] = report
        state["status"] = "succeeded"
        yield TraceEvent(EventName.VALIDATION, report.as_dict())

        # Said after the answer because it can only be known after the answer.
        for message in self._calculation_overridden(state, resolved.text):
            state["limitations"].append({"code": "calculation_overridden", "message": message})
            yield TraceEvent(
                EventName.LIMITATION, {"code": "calculation_overridden", "message": message}
            )

    @staticmethod
    def _calculation_overridden(state: AgentState, answer: str) -> list[str]:
        """Calculations the answer quietly declined to use.

        Synthesis is the terminal step, so anything a tool produces is produced
        *before* the answer exists — including a generated document, which is
        assembled from the tool results. That is fine while the two agree. It
        stops being fine the moment the model re-reads the sources while
        writing and decides the tool was working from the wrong inputs.

        Which is exactly what happened on a real run: asked for the 2023 and
        2029 readings, the model handed the calculator the 2019 and 2023 ones,
        got a correct 0.2333 mm/year for the numbers it supplied, and the
        report was compiled from it. Then, writing the answer, it noticed,
        recomputed 0.55 mm/year from the right pair, and said so on screen. The
        screen was right. The signed, downloadable document said 0.2333, and
        nothing in the run mentioned that the two disagreed.

        A wrong number in a document nobody flagged is worse than no document,
        so the run says it. Which of the two is correct is deliberately left
        open — the tool did the arithmetic it was given, and whether the inputs
        were the right ones is a question only a person can settle.
        """
        from workbench.agent import numerics

        answer_values: list[float] = []
        for figure in numerics.extract(answer):
            try:
                answer_values.append(float(figure.value))
            except ValueError:
                continue

        produced_document = any(
            (result.get("artifacts") or []) for result in state.get("tool_results") or []
        )

        messages: list[str] = []
        for result in state.get("tool_results") or []:
            display = result.get("display") or {}
            if display.get("kind") != "calculation" or not result.get("ok"):
                continue
            try:
                computed = float(display["value"])
            except (KeyError, TypeError, ValueError):
                continue
            # Compared with tolerance: an answer that rounds 0.2333 to 0.23 has
            # used the result, and flagging that would train the reader to
            # ignore the notice.
            tolerance = max(abs(computed) * 0.02, 1e-9)
            if any(abs(value - computed) <= tolerance for value in answer_values):
                continue

            note = (
                f"The answer does not use the {display.get('calculation', 'calculation')} "
                f"result, which computed {display.get('formatted', computed)} from the inputs "
                f"it was given."
            )
            if produced_document:
                note += (
                    " The generated document was assembled from that result, so the document "
                    "and this answer disagree — read the document before sending it on."
                )
            messages.append(note)
        return messages

    def _synthesis_input(self, state: AgentState, evidence: list[EvidenceItem]) -> str:
        parts = [_with_history(state), "", build_evidence_prompt(evidence)]
        if state["scratchpad"]:
            parts += ["", "Tool results:", json.dumps(state["scratchpad"], indent=2)[:4000]]
        if state["budget"].exhausted:
            parts += [
                "",
                f"NOTE: the run {state['budget'].reason()}. Answer with what is "
                f"available and state plainly what could not be completed.",
            ]
        for limitation in state.get("limitations") or []:
            parts += [
                "",
                f"NOTE: {limitation['message']} Open the answer by saying this "
                f"plainly, in one sentence, before the findings.",
            ]
        return "\n".join(parts)

    async def _validate(self, state: AgentState, resolved: Any) -> ValidationReport:
        """The critic. Cheap checks first; the model only judges what is flagged."""
        import re

        report = ValidationReport(unresolved_citations=list(resolved.unresolved))

        # Figures are checked even on an answer that fails every other test:
        # "where did that number come from" is the question worth answering
        # regardless of how well the paragraph around it is cited.
        report.figures = [
            f.as_dict()
            for f in numerics.verify(
                resolved.text,
                self._cited_text(state, resolved),
                numerics.calculated(state.get("tool_results") or []),
            )
        ]

        sentences = [
            s.strip() for s in re.split(r"(?<=[.!?])\s+", resolved.text) if len(s.strip()) > 25
        ]

        # A refusal has nothing to ground, so it exits before the citation
        # checks below rather than being scored 0% for correctly declining.
        if is_refusal(resolved.text):
            report.is_refusal = True
            report.grounded_ratio = 1.0
            return report

        if not sentences:
            return report
        if not resolved.citations:
            # An answer with substance and no citations is exactly what this
            # system exists to prevent.
            report.grounded_ratio = 0.0
            report.unsupported = sentences[:5]
            return report

        report.grounded_ratio, report.unsupported = self._coverage(resolved.text)
        return report

    @staticmethod
    def _cited_text(state: AgentState, resolved: Any) -> dict[int, str]:
        """The full passage behind each citation number, for figure checking.

        The snippet shown in the UI is truncated; a figure quoted from the end
        of a long chunk would read as unverified against it. This uses the
        whole retrieved text.
        """
        by_chunk = {item.chunk_id: item.text for item in state.get("evidence", [])}
        return {
            citation.n: by_chunk[citation.chunk_id]
            for citation in resolved.citations
            if citation.chunk_id in by_chunk
        }

    @staticmethod
    def _coverage(text: str) -> tuple[float, list[str]]:
        """What fraction of the answer's claims carry a citation.

        Counted per paragraph rather than per sentence. Models cite the way
        people do — once at the end of a passage, and often on its own line
        after it — so demanding a marker inside every sentence reports a
        correctly-cited answer as completely ungrounded. A false alarm here is
        worse than no metric at all: it teaches the reader to ignore the badge,
        and the badge is the whole point.
        """
        import re

        marker = re.compile(r"\[\d+\]")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

        # A paragraph that is nothing but markers is the citation for the one
        # before it, not an uncited claim of its own.
        merged: list[str] = []
        for paragraph in paragraphs:
            if merged and not marker.sub("", paragraph).strip():
                merged[-1] = f"{merged[-1]} {paragraph}"
            else:
                merged.append(paragraph)

        # A claim is a paragraph with enough words to assert something. Counted
        # in words rather than characters: "The limit is 2 bar per minute [1]."
        # is a real claim but only 22 letters, and a character threshold
        # silently dropped exactly the short, well-cited sentences this system
        # is trying to encourage.
        def is_claim(paragraph: str) -> bool:
            body = marker.sub("", paragraph).strip()
            if body.startswith("#"):  # a markdown heading asserts nothing
                return False
            return len(re.findall(r"[A-Za-z][A-Za-z'-]*", body)) >= 4

        claim_paragraphs = [p for p in merged if is_claim(p)]
        if not claim_paragraphs:
            return 1.0, []

        cited = [p for p in claim_paragraphs if marker.search(p)]
        uncited = [p for p in claim_paragraphs if not marker.search(p)]
        return len(cited) / len(claim_paragraphs), [p[:200] for p in uncited[:5]]

    # ------------------------------------------------------------- helpers
    async def _generate(
        self,
        logical_name: str,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        state: AgentState,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Any:
        """One non-streaming model call, with the budget updated.

        `max_tokens` covers the model's *reasoning* as well as its output.
        That is the trap: a thinking model can spend the entire allowance
        deliberating and emit a truncated answer, and a truncated JSON object
        is indistinguishable from a model that cannot follow a schema.
        """
        info = self.registry.get_model(logical_name)
        provider = self.registry.provider_for(logical_name)
        request = GenerationRequest(
            model=info.physical_id,
            messages=messages,
            temperature=0.0,
            json_schema=json_schema,
            num_ctx=info.default_num_ctx,
            max_tokens=max_tokens,
        )
        if self.residency is not None:
            await self.residency.acquire(logical_name)
        try:
            result = await provider.generate(request)
        except ProviderResponseError as exc:
            # LM Studio unloads idle models on its own timer, and residency
            # only hears about it at the next reconciliation. In between, the
            # stale entry makes acquire() a no-op and the call fails with
            # "Model unloaded" — which killed an entire run at its first
            # model call, on a machine where the model was one load away.
            # Correct the belief and try exactly once more; a second failure
            # is a real one.
            if "model unloaded" not in str(exc).lower() or self.residency is None:
                raise
            log.warning("model_unloaded_underneath_us", model=logical_name)
            self.residency.forget(logical_name)
            await self.residency.acquire(logical_name)
            result = await provider.generate(request)
        state["budget"].tokens_used += result.usage.total_tokens
        return result
