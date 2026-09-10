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
    error: str | None = None

    @classmethod
    def failure(cls, error: str, **metrics: Any) -> ToolResult:
        return cls(ok=False, error=error, metrics=metrics)

    def summary(self) -> dict[str, Any]:
        """A compact form for the trace timeline and the scratchpad.

        Full payloads stay out of the agent's context: a table query returning
        4,000 rows must not consume the window that the answer needs.
        """
        return {
            "ok": self.ok,
            "error": self.error,
            "citations": len(self.citations),
            "artifacts": [a.get("filename", a.get("artifact_id", "")) for a in self.artifacts],
            "metrics": self.metrics,
        }


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
