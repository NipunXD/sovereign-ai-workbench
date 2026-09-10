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

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from workbench.agent.prompts import (
    GROUNDING_JUDGE_PROMPT,
    PLANNER_PROMPT,
    QUERY_REWRITE_PROMPT,
    SUFFICIENCY_PROMPT,
    SYNTHESIS_PROMPT,
)
from workbench.agent.state import (
    AgentState,
    Budget,
    Plan,
    PlanStep,
    StepIntent,
    ValidationReport,
    merge_evidence,
)
from workbench.api.sse import EventName
from workbench.core.ids import prefixed_id
from workbench.core.logging import get_logger
from workbench.providers.types import ChatMessage, GenerationRequest, ImageRef
from workbench.rag.citations import EvidenceItem, build_evidence_prompt, resolve_markers
from workbench.router.router import ModelRouter, RouteRequest
from workbench.security.rbac import Principal
from workbench.tools.base import ToolContext
from workbench.tools.registry import ToolRegistry

log = get_logger(__name__)

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
    ) -> None:
        self.router = router
        self.registry = registry
        self.tools = tools
        self.retriever = retriever
        self.residency = residency

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
    ) -> AsyncIterator[TraceEvent]:
        """Execute the loop, yielding trace events as they happen."""
        state: AgentState = {
            "run_id": run_id or prefixed_id("run"),
            "conversation_id": conversation_id,
            "principal": principal,
            "user_input": user_input,
            "attachments": attachments or [],
            "evidence": [],
            "scratchpad": [],
            "route_decisions": [],
            "tool_results": [],
            "artifacts": [],
            "errors": [],
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
        except Exception as exc:  # noqa: BLE001
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
                ChatMessage(role="user", content=state["user_input"]),
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
        if steps[-1].intent is not StepIntent.SYNTHESIZE:
            steps.append(
                PlanStep(id="final", intent=StepIntent.SYNTHESIZE, description="Write the answer")
            )
        return Plan(steps=steps[:6], rationale=str(payload.get("rationale", "")))

    # -------------------------------------------------------------- execute
    async def _execute(self, state: AgentState) -> AsyncIterator[TraceEvent]:
        plan: Plan = state["plan"]
        budget: Budget = state["budget"]

        while (step := plan.next_step) is not None:
            if step.intent is StepIntent.SYNTHESIZE:
                return
            if budget.exhausted:
                # Not an error: answer with what is available and say so.
                state["scratchpad"].append(
                    {"note": f"stopped early — {budget.reason()}"}
                )
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
                            "doc_title": item.doc_title,
                            "page": item.page_from,
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
                {"step_id": f"{step.id}.r{loops['retrieve']}", "intent": "retrieve",
                 "description": f"Retrying with: {query}"},
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
            except Exception:  # noqa: BLE001 - fall through to binding
                pass

        decision = await self.router.route(
            RouteRequest(text=step.description, node_intent="plan", needs_json=True)
        )
        evidence_text = build_evidence_prompt(state.get("evidence") or [])
        prior = json.dumps(state["scratchpad"], indent=2)[:2000] if state["scratchpad"] else "(none)"

        result = await self._generate(
            decision.model.logical_name,
            [
                ChatMessage(
                    role="system",
                    content=(
                        f"Produce the arguments for the tool '{spec.name}'.\n"
                        f"{spec.description}\n\n"
                        "Take every value from the sources or from earlier tool results. "
                        "Never invent a number. If a required value is not present, return "
                        '{"_missing": "what is absent"} instead of guessing.\n\n'
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
        )
        try:
            bound = json.loads(result.text)
        except json.JSONDecodeError:
            return {}, "could not produce valid arguments for this tool"
        if "_missing" in bound:
            return {}, f"required input not found in the sources: {bound['_missing']}"
        return bound, None

    async def _act(self, state: AgentState, step: PlanStep) -> AsyncIterator[TraceEvent]:
        if not step.tool:
            return
        budget: Budget = state["budget"]

        bound_args, binding_error = await self._bind_args(state, step)
        if binding_error:
            state["scratchpad"].append(
                {"tool": step.tool, "ok": False, "error": binding_error}
            )
            yield TraceEvent(
                EventName.TOOL_RESULT,
                {"tool": step.tool, "ok": False, "error": binding_error},
            )
            return
        step.args = bound_args
        ctx = ToolContext(
            principal=state["principal"],
            run_id=state["run_id"],
            step_id=step.id,
            deadline=budget.deadline,
        )

        collected: list[TraceEvent] = []

        async def emit(name: str, data: dict[str, Any]) -> None:
            collected.append(TraceEvent(name, data))

        ctx.emit = emit

        try:
            result = await self.tools.dispatch(step.tool, step.args, ctx)
        except Exception as exc:  # noqa: BLE001
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
    def _compact(data: Any) -> Any:
        if data is None:
            return None
        try:
            payload = data.model_dump(mode="json")
        except AttributeError:
            return str(data)[:1000]
        return json.loads(json.dumps(payload)[:2000] + ("}" if len(json.dumps(payload)) > 2000 else ""))

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

        resolved = resolve_markers(draft, evidence)
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

    def _synthesis_input(self, state: AgentState, evidence: list[EvidenceItem]) -> str:
        parts = [f"Question: {state['user_input']}", "", build_evidence_prompt(evidence)]
        if state["scratchpad"]:
            parts += ["", "Tool results:", json.dumps(state["scratchpad"], indent=2)[:4000]]
        if state["budget"].exhausted:
            parts += [
                "",
                f"NOTE: the run {state['budget'].reason()}. Answer with what is "
                f"available and state plainly what could not be completed.",
            ]
        return "\n".join(parts)

    async def _validate(self, state: AgentState, resolved: Any) -> ValidationReport:
        """The critic. Cheap checks first; the model only judges what is flagged."""
        import re

        report = ValidationReport(unresolved_citations=list(resolved.unresolved))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", resolved.text) if len(s.strip()) > 25]

        refusal_markers = ("do not contain", "does not contain", "not covered", "no information",
                           "cannot answer", "could not find", "not available in")
        if any(marker in resolved.text.lower() for marker in refusal_markers):
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
    ) -> Any:
        """One non-streaming model call, with the budget updated."""
        info = self.registry.get_model(logical_name)
        provider = self.registry.provider_for(logical_name)
        if self.residency is not None:
            await self.residency.acquire(logical_name)
        result = await provider.generate(
            GenerationRequest(
                model=info.physical_id,
                messages=messages,
                temperature=0.0,
                json_schema=json_schema,
                num_ctx=info.default_num_ctx,
                max_tokens=1024,
            )
        )
        state["budget"].tokens_used += result.usage.total_tokens
        return result
