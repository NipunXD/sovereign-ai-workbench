"""A deterministic sandbox for tests and offline development.

Runs nothing. It exists so the agent graph, the tool registry and the approval
gate can be exercised in CI where there is no Docker daemon — and so a test that
is really about approval flow does not spend eight seconds starting a container.

It deliberately mirrors the real backend's *refusals*: the static check still
runs, so a test asserting that networking is rejected passes here for the same
reason it passes against Docker.
"""

from __future__ import annotations

import time
from pathlib import Path

from workbench.sandbox.base import (
    OutputFile,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)
from workbench.sandbox.static_check import check


class FakeSandbox:
    """Replays canned results, keyed by a substring of the code."""

    name = "fake"

    def __init__(self, *, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root
        #: substring -> (status, stdout). Set by a test to script an outcome.
        self.responses: dict[str, tuple[SandboxStatus, str]] = {}
        #: Files a scripted run should appear to have produced.
        self.output_files: list[OutputFile] = []
        self.calls: list[SandboxJob] = []

    async def health(self) -> tuple[bool, str]:
        return True, "fake sandbox (executes nothing)"

    async def execute(self, job: SandboxJob) -> SandboxResult:
        self.calls.append(job)
        started = time.perf_counter()

        # The same refusal the real backend applies, so a policy test does not
        # silently pass for a different reason here.
        verdict = check(job.code)
        if not verdict.ok:
            return SandboxResult(
                status=SandboxStatus.POLICY,
                error=f"the generated code was refused: {verdict.summary}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        for marker, (status, stdout) in self.responses.items():
            if marker in job.code:
                return SandboxResult(
                    status=status,
                    exit_code=0 if status is SandboxStatus.OK else 1,
                    stdout=stdout,
                    output_files=list(self.output_files),
                    duration_ms=1,
                    limits={"network": "none", "backend": "fake"},
                )

        return SandboxResult(
            status=SandboxStatus.OK,
            exit_code=0,
            stdout="[fake sandbox] nothing was executed",
            output_files=list(self.output_files),
            duration_ms=1,
            limits={"network": "none", "backend": "fake"},
        )


def build_backend(name: str, *, workspace_root: Path, image: str = "workbench/sandbox:0.1.0"):
    """Select a sandbox backend by name."""
    if name == "docker":
        from workbench.sandbox.docker_backend import DockerSandbox

        return DockerSandbox(image=image, workspace_root=workspace_root)
    if name == "fake":
        return FakeSandbox(workspace_root=workspace_root)
    from workbench.core.errors import ConfigurationError

    raise ConfigurationError(f"unknown sandbox backend '{name}'")
