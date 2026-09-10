"""Docker-backed execution of model-generated code.

The security claim this makes is narrow and checkable: code produced by a model
runs with no network interface, a read-only root filesystem, no capabilities, a
non-root user, and hard limits on memory, CPU, processes and file size.

`network_mode="none"` is the load-bearing control. It is a kernel-level
property — the container has a loopback interface and nothing else — rather
than a proxy rule or an allowlist that could be misconfigured. Code that tries
to open a socket fails because there is no route to anywhere, not because
something inspected its intent.

Everything applied is recorded on the result, so "the code could not phone home"
is a claim a reviewer can verify against the audit record rather than take on
trust.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from workbench.core.hashing import digest_bytes
from workbench.core.logging import get_logger
from workbench.sandbox.base import (
    OutputFile,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)
from workbench.sandbox.static_check import check

log = get_logger(__name__)

#: Containers this backend created, so orphans from a crashed process can be
#: reaped on the next startup rather than accumulating.
CONTAINER_PREFIX = "workbench-sbx-"


class DockerSandbox:
    """Runs code in a disposable, network-less container."""

    name = "docker"

    def __init__(
        self,
        *,
        image: str = "workbench/sandbox:0.1.0",
        workspace_root: Path,
        max_concurrent: int = 2,
    ) -> None:
        self.image = image
        self.workspace_root = workspace_root
        # Two at a time. The machine is also running inference; more concurrent
        # containers means every one of them, and the model, gets slower.
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client: Any = None

    def _docker(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def health(self) -> tuple[bool, str]:
        try:
            client = await asyncio.to_thread(self._docker)
            await asyncio.to_thread(client.ping)
            images = await asyncio.to_thread(client.images.list, self.image)
            if not images:
                return False, f"image '{self.image}' is not built — run `make sandbox-image`"
            return True, "ready"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    async def execute(self, job: SandboxJob) -> SandboxResult:
        started = time.perf_counter()
        code_digest = digest_bytes(job.code.encode("utf-8"))

        # Refuse before starting a container. Cheaper, and the message names the
        # line rather than surfacing as a confusing runtime error.
        verdict = check(job.code)
        if not verdict.ok:
            return SandboxResult(
                status=SandboxStatus.POLICY,
                error=f"the generated code was refused: {verdict.summary}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                code_digest=code_digest,
                limits=self._limits(job),
            )

        async with self._semaphore:
            return await asyncio.to_thread(self._run, job, code_digest, started)

    def _limits(self, job: SandboxJob) -> dict[str, Any]:
        """The applied configuration, for the audit record."""
        return {
            "network": "none",
            "read_only_rootfs": True,
            "user": "65534:65534",
            "cap_drop": "ALL",
            "no_new_privileges": True,
            "memory_mb": job.memory_mb,
            "memswap_mb": job.memory_mb,  # equal to memory: no swap, so OOM is real
            "cpus": job.cpus,
            "pids_limit": 128,
            "timeout_s": job.timeout_s,
            "max_file_mb": job.max_file_mb,
            "image": self.image,
        }

    def _run(self, job: SandboxJob, code_digest: str, started: float) -> SandboxResult:
        import docker
        from docker.errors import ImageNotFound

        client = self._docker()
        workspace = Path(tempfile.mkdtemp(prefix="sbx-", dir=self.workspace_root))
        in_dir, out_dir = workspace / "in", workspace / "out"
        in_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        (in_dir / "job.json").write_text(
            json.dumps({"code": job.code, "max_file_bytes": job.max_file_mb * 1024 * 1024}),
            encoding="utf-8",
        )
        for name, data in job.input_files.items():
            # Flattened deliberately: a name like "../../etc/passwd" must not
            # escape the input directory.
            (in_dir / Path(name).name).write_bytes(data)

        # World-readable/writable because the container runs as uid 65534, which
        # does not match the host user that owns these directories.
        for path in (in_dir, out_dir):
            path.chmod(0o777)
        (in_dir / "job.json").chmod(0o444)

        container = None
        image_digest = ""
        try:
            try:
                image = client.images.get(self.image)
                image_digest = (image.id or "")[:19]
            except ImageNotFound:
                return SandboxResult(
                    status=SandboxStatus.ERROR,
                    error=f"the sandbox image '{self.image}' is not built",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    code_digest=code_digest,
                    limits=self._limits(job),
                )

            container = client.containers.run(
                self.image,
                detach=True,
                name=f"{CONTAINER_PREFIX}{code_digest[:12]}-{int(time.time() * 1000)}",
                # --- the isolation ---
                network_mode="none",          # no interface at all, kernel-level
                read_only=True,               # the image cannot be modified
                user="65534:65534",           # nobody
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                # /tmp is the one writable scratch area, and nothing there may
                # be executed.
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                mounts=[
                    docker.types.Mount("/workspace/in", str(in_dir), type="bind", read_only=True),
                    docker.types.Mount("/workspace/out", str(out_dir), type="bind"),
                ],
                mem_limit=f"{job.memory_mb}m",
                # Equal to mem_limit so there is no swap to fall back on:
                # exceeding memory produces a real OOM rather than thrashing.
                memswap_limit=f"{job.memory_mb}m",
                nano_cpus=int(job.cpus * 1e9),
                pids_limit=128,
                environment={"PYTHONHASHSEED": "0", "MPLBACKEND": "Agg", "HOME": "/tmp"},
            )

            try:
                wait_result = container.wait(timeout=job.timeout_s)
                exit_code = int(wait_result.get("StatusCode", -1))
            except Exception:  # noqa: BLE001 - requests timeout type varies by version
                container.kill()
                return SandboxResult(
                    status=SandboxStatus.TIMEOUT,
                    error=f"execution exceeded the {job.timeout_s}s limit and was terminated",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    code_digest=code_digest,
                    image_digest=image_digest,
                    limits=self._limits(job),
                )

            raw_stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            raw_stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")

            # 137 is SIGKILL, which for a memory-limited container means the
            # OOM killer rather than a timeout.
            if exit_code == 137:
                # `attrs` is a snapshot taken at creation, when the container
                # had not run yet — OOMKilled reads false there no matter what
                # happened. It has to be refetched.
                container.reload()
                state = container.attrs.get("State", {})
                if state.get("OOMKilled"):
                    return SandboxResult(
                        status=SandboxStatus.MEMORY,
                        exit_code=exit_code,
                        stderr=raw_stderr[:8000],
                        error=f"the code exceeded the {job.memory_mb} MB memory limit",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        code_digest=code_digest,
                        image_digest=image_digest,
                        limits=self._limits(job),
                    )

            payload = self._parse(raw_stdout)
            files = self._collect(out_dir, payload.get("output_files", []))
            status = {
                "ok": SandboxStatus.OK,
                "memory": SandboxStatus.MEMORY,
                "error": SandboxStatus.ERROR,
            }.get(str(payload.get("status", "error")), SandboxStatus.ERROR)

            return SandboxResult(
                status=status,
                exit_code=exit_code,
                stdout=str(payload.get("stdout", ""))[:64_000],
                stderr=str(payload.get("stderr", raw_stderr))[:64_000],
                error=payload.get("error"),
                output_files=files,
                duration_ms=int((time.perf_counter() - started) * 1000),
                code_digest=code_digest,
                image_digest=image_digest,
                limits=self._limits(job),
            )

        except Exception as exc:  # noqa: BLE001
            log.exception("sandbox_execution_failed", error=str(exc))
            return SandboxResult(
                status=SandboxStatus.ERROR,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                code_digest=code_digest,
                image_digest=image_digest,
                limits=self._limits(job),
            )
        finally:
            # Always remove the container. A leaked one holds its memory
            # reservation and, over a long session, the machine.
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001
                    log.warning("sandbox_container_remove_failed")

    @staticmethod
    def _parse(raw_stdout: str) -> dict[str, Any]:
        """The harness prints one JSON object as its last line."""
        for line in reversed(raw_stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {"status": "error", "error": "the harness produced no result", "stdout": raw_stdout}

    @staticmethod
    def _collect(out_dir: Path, reported: list[dict[str, Any]]) -> list[OutputFile]:
        files = []
        for entry in reported:
            path = out_dir / str(entry.get("name", ""))
            if path.is_file():
                files.append(
                    OutputFile(
                        name=str(entry.get("name")),
                        size_bytes=int(entry.get("size_bytes", 0)),
                        path=path,
                        suffix=str(entry.get("suffix", "")),
                    )
                )
        return files

    async def reap_orphans(self) -> int:
        """Remove containers left behind by a previous process.

        A crash mid-execution leaves a container running with its memory
        reservation held. Cleaning up at startup keeps that from accumulating
        across restarts.
        """
        try:
            client = await asyncio.to_thread(self._docker)
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters={"name": CONTAINER_PREFIX}
            )
        except Exception:  # noqa: BLE001
            return 0

        removed = 0
        for container in containers:
            try:
                await asyncio.to_thread(container.remove, force=True)
                removed += 1
            except Exception:  # noqa: BLE001
                continue
        if removed:
            log.info("sandbox_orphans_reaped", count=removed)
        return removed
