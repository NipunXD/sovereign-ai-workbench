"""The tool contract.

Three rules make tool use safe enough to hand an autonomous agent:

* **Permissions come from the principal, never the prompt.** A tool the caller
  may not use is absent from the catalogue the planner sees, so the model cannot
  propose it, let alone invoke it.
* **The dispatcher decides approval, not the model.** ``requires_approval`` is
  read from the spec and policy. A model cannot talk its way past a gate because
  it is never asked.
* **Arguments are validated against a schema before execution.** A tool receives
  a parsed, typed object or the call fails — never a raw dict from a model.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from workbench.core.errors import ToolError
from workbench.core.hashing import digest
from workbench.providers.types import ToolSchema
from workbench.security.rbac import Principal

#: How much a tool changes the world. Drives approval policy and audit severity.
SideEffect = Literal["none", "read", "write", "execute"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Everything the system needs to know about a tool without running it."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    version: str = "1.0.0"
    required_permissions: frozenset[str] = frozenset()
    side_effect: SideEffect = "none"
    requires_approval: bool = False
    timeout_s: int = 60
    max_concurrency: int = 4
    #: Forces a routing lane when the tool calls a model itself.
    lane_hint: str | None = None
    #: Output fields scrubbed before the result reaches the audit log.
    redact_output_fields: tuple[str, ...] = ()

    def to_schema(self) -> ToolSchema:
        """Render for a model's tool-calling API."""
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )

    def available_to(self, principal: Principal) -> bool:
        return self.required_permissions <= principal.permissions


@dataclass
class ToolContext:
    """Per-invocation context. Explicit, so there is no ambient authority."""

    principal: Principal
    run_id: str = ""
    step_id: str = ""
    #: Scratch directory for this run, mounted into the sandbox when used.
    workspace: Path | None = None
    #: Emits a trace event to the live SSE stream.
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    #: Wall-clock deadline inherited from the run's budget.
    deadline: float = 0.0
    #: What the run has established so far — models used, evidence cited, tools
    #: called, who approved. Provenance is built from this rather than from the
    #: model's arguments, because a model asked to state its own sources is free
    #: to invent them, and the entire point of the block is that it cannot.
    run_context: dict[str, Any] = field(default_factory=dict)

    async def trace(self, name: str, data: dict[str, Any]) -> None:
        if self.emit is not None:
            await self.emit(name, data)

    @property
    def remaining_s(self) -> float:
        if not self.deadline:
            return float("inf")
        return max(0.0, self.deadline - time.monotonic())


@dataclass
class ToolResult:
    """What a tool returns.

    Carries citations and artifacts alongside the data so the agent does not
    have to know each tool's internals to attribute or collect its output.
    """

    ok: bool = True
    data: BaseModel | None = None
    citations: list[Any] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    logs: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    #: A small, already-formatted view of the result for the trace panel.
    #: Separate from ``data`` because that is sized for the model's context and
    #: deliberately kept out of the timeline; this is sized for a person, and a
    #: calculation whose working nobody can see is just a number from a model
    #: again.
    display: dict[str, Any] | None = None
    error: str | None = None
    #: Whether the tool declined its inputs rather than breaking on them.
    #: A dimensional check that catches a bad unit, or a reading the documents
    #: put in a different year, is this control working — and reporting it the
    #: same way as a crash teaches a reader that the red line means nothing.
    refused: bool = False

    @classmethod
    def failure(cls, error: str, **metrics: Any) -> ToolResult:
        """The tool broke: a timeout, an exception, a backend that was gone."""
        return cls(ok=False, error=error, metrics=metrics)

    @classmethod
    def refuse(cls, reason: str, **metrics: Any) -> ToolResult:
        """The tool worked and declined the inputs it was given.

        Kept apart from :meth:`failure` because the two need opposite
        readings. A failure means something is wrong with the system; a
        refusal means the system caught something wrong with the request,
        which is the whole reason a calculation goes through a tool instead of
        a sentence.
        """
        return cls(ok=False, error=reason, refused=True, metrics=metrics)

    def summary(self) -> dict[str, Any]:
        """A compact form for the trace timeline and the scratchpad.

        Full payloads stay out of the agent's context: a table query returning
        4,000 rows must not consume the window that the answer needs.
        """
        summary: dict[str, Any] = {
            "ok": self.ok,
            "error": self.error,
            "citations": len(self.citations),
            "artifacts": [a.get("filename", a.get("artifact_id", "")) for a in self.artifacts],
            "metrics": self.metrics,
        }
        if self.display is not None:
            summary["display"] = self.display
        if self.refused:
            summary["refused"] = True
        return summary


@runtime_checkable
class Tool(Protocol):
    """An action the agent can take."""

    spec: ToolSpec

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


class BaseTool:
    """Convenience base handling validation, timing and error translation."""

    spec: ToolSpec

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def parse_args(self, raw: dict[str, Any]) -> BaseModel:
        """Validate model-supplied arguments against the declared schema.

        Models emit plausible-looking arguments that do not fit the schema
        regularly. Failing here with the validation detail lets the agent
        correct itself, rather than the tool crashing on a missing key.
        """
        try:
            return self.spec.input_model.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
            )
            raise ToolError(
                f"invalid arguments for '{self.spec.name}': {problems}",
                tool=self.spec.name,
            ) from exc

    @staticmethod
    def args_digest(args: BaseModel) -> str:
        return digest(args.model_dump(mode="json"))
