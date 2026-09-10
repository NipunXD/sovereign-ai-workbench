"""Agent run state.

The state a LangGraph run threads between nodes. Two things it is *not*: a place
to accumulate raw tool output, and a place to keep the conversation. Large
payloads stay in the store and are referenced here, because the context window
that would be consumed carrying a 4,000-row query result is the same window the
answer needs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from workbench.rag.citations import Citation, EvidenceItem
from workbench.security.rbac import Principal


class StepIntent(StrEnum):
    """What a plan step is for. Decides which node handles it."""

    RETRIEVE = "retrieve"
    TOOL = "tool"
    SYNTHESIZE = "synthesize"


@dataclass
class PlanStep:
    id: str
    intent: StepIntent
    description: str = ""
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    result_summary: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent.value,
            "description": self.description,
            "tool": self.tool,
            "done": self.done,
        }


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    rationale: str = ""

    @property
    def next_step(self) -> PlanStep | None:
        return next((s for s in self.steps if not s.done), None)

    @property
    def complete(self) -> bool:
        return all(s.done for s in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {"steps": [s.as_dict() for s in self.steps], "rationale": self.rationale}


@dataclass
class Budget:
    """Hard limits on a run.

    Without these an agent that misreads its own progress will loop until the
    user gives up. Exhaustion is not an error: the run proceeds to synthesis
    with whatever it has and says what it could not finish.
    """

    max_tool_calls: int = 12
    max_tokens: int = 60_000
    max_wall_s: float = 300.0

    tool_calls_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def deadline(self) -> float:
        return self.started_at + self.max_wall_s

    @property
    def exhausted(self) -> bool:
        return (
            self.tool_calls_used >= self.max_tool_calls
            or self.tokens_used >= self.max_tokens
            or self.elapsed_s >= self.max_wall_s
        )

    def reason(self) -> str:
        """Why the budget ran out, phrased for the user."""
        if self.tool_calls_used >= self.max_tool_calls:
            return f"reached the {self.max_tool_calls}-step limit for a single request"
        if self.tokens_used >= self.max_tokens:
            return f"reached the {self.max_tokens:,}-token limit for a single request"
        if self.elapsed_s >= self.max_wall_s:
            return f"reached the {self.max_wall_s:.0f}-second time limit"
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_calls_used": self.tool_calls_used,
            "max_tool_calls": self.max_tool_calls,
            "tokens_used": self.tokens_used,
            "max_tokens": self.max_tokens,
            "elapsed_s": round(self.elapsed_s, 1),
            "max_wall_s": self.max_wall_s,
        }


@dataclass
class ValidationReport:
    """The critic's verdict on a draft answer."""

    grounded_ratio: float = 1.0
    unsupported: list[str] = field(default_factory=list)
    #: Citation markers referring to chunks that were never retrieved.
    unresolved_citations: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    #: True when the answer correctly declines rather than guessing.
    is_refusal: bool = False

    @property
    def acceptable(self) -> bool:
        return not self.schema_errors and (self.is_refusal or self.grounded_ratio >= 0.8)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grounded_ratio": round(self.grounded_ratio, 3),
            "unsupported": self.unsupported,
            "unresolved_citations": self.unresolved_citations,
            "schema_errors": self.schema_errors,
            "is_refusal": self.is_refusal,
            "acceptable": self.acceptable,
        }


def merge_evidence(
    existing: list[EvidenceItem] | None, incoming: list[EvidenceItem] | None
) -> list[EvidenceItem]:
    """Accumulate retrieved evidence across retrieve loops, deduplicated.

    A LangGraph reducer. Without dedup, a second retrieval pass re-adds the same
    chunks and the model sees the same passage three times, which it reads as
    corroboration.
    """
    merged: dict[str, EvidenceItem] = {item.chunk_id: item for item in existing or []}
    for item in incoming or []:
        current = merged.get(item.chunk_id)
        if current is None or item.score > current.score:
            merged[item.chunk_id] = item
    return sorted(merged.values(), key=lambda i: -i.score)


def append_list(existing: list[Any] | None, incoming: list[Any] | None) -> list[Any]:
    """Reducer that appends rather than replaces."""
    return [*(existing or []), *(incoming or [])]


class AgentState(TypedDict, total=False):
    """The state threaded through the graph."""

    run_id: str
    conversation_id: str
    principal: Principal
    user_input: str
    attachments: list[dict[str, Any]]

    plan: Plan
    #: Accumulated across retrieve loops; deduplicated by the reducer.
    evidence: Annotated[list[EvidenceItem], merge_evidence]
    #: Compact observations, never raw tool payloads.
    scratchpad: Annotated[list[dict[str, Any]], append_list]
    route_decisions: Annotated[list[dict[str, Any]], append_list]
    tool_results: Annotated[list[dict[str, Any]], append_list]
    artifacts: Annotated[list[dict[str, Any]], append_list]

    draft: str
    final: str
    citations: list[Citation]
    validation: ValidationReport

    budget: Budget
    #: Per-loop counters, so a cycle cannot run forever.
    loops: dict[str, int]
    errors: Annotated[list[dict[str, Any]], append_list]
    #: Things the run could not do and the reason, in words meant for the
    #: person who asked. Distinct from `errors`, which records faults: a
    #: limitation is the system working correctly and declining.
    limitations: Annotated[list[dict[str, Any]], append_list]
    #: Set when the run halted for a human decision.
    approval: dict[str, Any] | None
    status: str
