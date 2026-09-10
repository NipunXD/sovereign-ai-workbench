"""The sandbox contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class SandboxStatus(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    MEMORY = "memory"
    ERROR = "error"
    #: Refused by the static check before a container was ever started.
    POLICY = "policy"


@dataclass
class SandboxJob:
    code: str
    #: Files copied into the read-only /workspace/in mount.
    input_files: dict[str, bytes] = field(default_factory=dict)
    timeout_s: int = 60
    memory_mb: int = 1024
    cpus: float = 2.0
    max_file_mb: int = 64


@dataclass
class OutputFile:
    name: str
    size_bytes: int
    path: Path
    suffix: str = ""


@dataclass
class SandboxResult:
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    output_files: list[OutputFile] = field(default_factory=list)
    duration_ms: int = 0
    #: Exactly what was applied, recorded so a reviewer can confirm after the
    #: fact that the code had no network access.
    limits: dict[str, Any] = field(default_factory=dict)
    code_digest: str = ""
    image_digest: str = ""

    @property
    def ok(self) -> bool:
        return self.status is SandboxStatus.OK


@runtime_checkable
class SandboxBackend(Protocol):
    name: str

    async def execute(self, job: SandboxJob) -> SandboxResult: ...
    async def health(self) -> tuple[bool, str]: ...
