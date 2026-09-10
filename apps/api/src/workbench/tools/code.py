"""Running generated Python.

Gated, because executing code a model wrote is the single most consequential
thing this system does. The gate is on the spec and enforced by the dispatcher;
the model is never asked whether its own code should run.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from workbench.core.logging import get_logger
from workbench.sandbox.base import SandboxJob, SandboxStatus
from workbench.tools.base import BaseTool, ToolContext, ToolResult, ToolSpec

log = get_logger(__name__)


class CodeInput(BaseModel):
    code: str = Field(
        description=(
            "Python to execute. numpy, pandas, matplotlib, openpyxl and pint are "
            "available. There is no network access. Write any output files into the "
            "current directory; they are collected and returned. Read input files from "
            "the IN_DIR path."
        )
    )
    timeout_s: int = Field(default=60, ge=1, le=300)
    purpose: str = Field(default="", description="What this computation is for")


class CodeOutput(BaseModel):
    status: str
    stdout: str
    stderr: str
    error: str | None = None
    output_files: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int
    limits: dict[str, Any] = Field(default_factory=dict)


class RunPythonTool(BaseTool):
    spec = ToolSpec(
        name="code.run_python",
        description=(
            "Execute Python in an isolated container with no network access, for "
            "calculations or charts that the fixed calculation catalogue does not "
            "cover. Prefer calc.engineering when it has the calculation you need — it "
            "checks units and shows its working, which arbitrary code does not."
        ),
        input_model=CodeInput,
        output_model=CodeOutput,
        required_permissions=frozenset({"tool:code_exec"}),
        side_effect="execute",
        requires_approval=True,
        timeout_s=300,
        max_concurrency=2,
    )

    def __init__(self, backend: Any, *, artifact_files: dict[str, bytes] | None = None) -> None:
        self.backend = backend
        #: Files produced by executions in this run, so a later artifact step can
        #: embed a chart this one generated.
        self.artifact_files = artifact_files if artifact_files is not None else {}

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, CodeInput)

        result = await self.backend.execute(SandboxJob(code=args.code, timeout_s=args.timeout_s))

        files: list[dict[str, Any]] = []
        for output in result.output_files:
            try:
                self.artifact_files[output.name] = output.path.read_bytes()
            except OSError:
                continue
            files.append(
                {"name": output.name, "size_bytes": output.size_bytes, "suffix": output.suffix}
            )

        payload = CodeOutput(
            status=result.status.value,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.error,
            output_files=files,
            duration_ms=result.duration_ms,
            limits=result.limits,
        )

        if result.status is not SandboxStatus.OK:
            # Returned as a failed result rather than raised: the agent should
            # see the error and be able to correct its code.
            return ToolResult(
                ok=False,
                data=payload,
                error=result.error or f"execution finished with status {result.status.value}",
                metrics={
                    "status": result.status.value,
                    "duration_ms": result.duration_ms,
                    "code_digest": result.code_digest,
                },
            )

        return ToolResult(
            ok=True,
            data=payload,
            metrics={
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "files": len(files),
                "code_digest": result.code_digest,
            },
        )
