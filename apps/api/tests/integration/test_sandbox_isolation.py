"""Sandbox isolation, against a real Docker daemon.

These are the tests that back the strongest claim this system makes: that code
written by a model cannot reach the network, cannot modify anything outside its
workspace, and cannot consume the machine.

Every one of them *attempts* the thing and asserts it fails. A test that merely
checked the configuration flags would pass just as happily against a container
that was never actually isolated.
"""

from __future__ import annotations

import pytest

from workbench.sandbox.base import SandboxJob, SandboxStatus
from workbench.sandbox.docker_backend import DockerSandbox

pytestmark = [pytest.mark.docker, pytest.mark.integration, pytest.mark.slow]


@pytest.fixture
async def sandbox(tmp_path):
    box = DockerSandbox(workspace_root=tmp_path)
    healthy, detail = await box.health()
    if not healthy:
        pytest.skip(f"docker sandbox unavailable: {detail}")
    return box


async def test_ordinary_calculation_runs(sandbox: DockerSandbox) -> None:
    result = await sandbox.execute(
        SandboxJob(
            code=(
                "import numpy as np\n"
                "readings = np.array([12.5, 9.2])\n"
                "print(f'{(readings[0] - readings[1]) / 6:.4f}')"
            ),
            timeout_s=60,
        )
    )
    assert result.status is SandboxStatus.OK, result.error
    assert "0.5500" in result.stdout


async def test_generated_files_come_back(sandbox: DockerSandbox) -> None:
    result = await sandbox.execute(
        SandboxJob(
            code=(
                "import matplotlib.pyplot as plt\n"
                "plt.plot([2019, 2023, 2029], [13.9, 12.5, 9.2])\n"
                "plt.savefig('trend.png')"
            ),
            timeout_s=90,
        )
    )
    assert result.status is SandboxStatus.OK, result.error
    assert [f.name for f in result.output_files] == ["trend.png"]
    assert result.output_files[0].size_bytes > 0
    assert result.output_files[0].path.is_file()


async def test_input_files_are_readable_and_the_mount_is_read_only(
    sandbox: DockerSandbox,
) -> None:
    result = await sandbox.execute(
        SandboxJob(
            code=(
                "from pathlib import Path\n"
                "print(Path(IN_DIR, 'readings.csv').read_text().strip())\n"
                "try:\n"
                "    Path(IN_DIR, 'readings.csv').write_text('tampered')\n"
                "    print('WROTE_TO_INPUT')\n"
                "except OSError as exc:\n"
                "    print('input is read-only')\n"
            ),
            input_files={"readings.csv": b"cml,mm\nCML-04,9.2"},
            timeout_s=60,
        )
    )
    assert result.status is SandboxStatus.OK, result.error
    assert "CML-04,9.2" in result.stdout
    assert "WROTE_TO_INPUT" not in result.stdout
    assert "input is read-only" in result.stdout


async def test_the_root_filesystem_cannot_be_written(sandbox: DockerSandbox) -> None:
    result = await sandbox.execute(
        SandboxJob(code="open('/etc/passwd', 'w').write('pwned')", timeout_s=30)
    )
    assert result.status is not SandboxStatus.OK
    assert "read-only" in (result.error or "").lower()


async def test_a_runaway_loop_is_terminated(sandbox: DockerSandbox) -> None:
    result = await sandbox.execute(SandboxJob(code="while True: pass", timeout_s=5))
    assert result.status is SandboxStatus.TIMEOUT
    # Comfortably bounded: the point is that it stopped, not that it stopped fast.
    assert result.duration_ms < 30_000


async def test_excess_memory_is_reported_as_such(sandbox: DockerSandbox) -> None:
    """Distinguished from a generic error so the agent can respond usefully."""
    result = await sandbox.execute(
        SandboxJob(
            code="x = bytearray(3 * 1024 * 1024 * 1024)",
            timeout_s=60,
            memory_mb=256,
        )
    )
    assert result.status is SandboxStatus.MEMORY
    assert "memory" in (result.error or "").lower()


async def test_networking_is_refused_before_a_container_starts(
    sandbox: DockerSandbox,
) -> None:
    """The static check catches the obvious form and explains itself."""
    result = await sandbox.execute(
        SandboxJob(code="import socket\nsocket.create_connection(('1.1.1.1', 80))")
    )
    assert result.status is SandboxStatus.POLICY
    assert "socket" in (result.error or "")
    # Refused without paying for a container.
    assert result.duration_ms < 500


async def test_every_result_records_what_was_applied(sandbox: DockerSandbox) -> None:
    """A reviewer must be able to confirm the isolation after the fact."""
    result = await sandbox.execute(SandboxJob(code="print('hello')", timeout_s=30))
    assert result.limits["network"] == "none"
    assert result.limits["read_only_rootfs"] is True
    assert result.limits["cap_drop"] == "ALL"
    assert result.limits["user"] == "65534:65534"
    # No swap to fall back on, so a memory limit is a real limit.
    assert result.limits["memswap_mb"] == result.limits["memory_mb"]
    assert result.code_digest
    assert result.image_digest


async def test_containers_do_not_survive_execution(sandbox: DockerSandbox) -> None:
    """A leaked container holds its memory reservation for the session."""
    import docker

    await sandbox.execute(SandboxJob(code="print('transient')", timeout_s=30))
    client = docker.from_env()
    remaining = client.containers.list(all=True, filters={"name": "workbench-sbx-"})
    assert remaining == []
