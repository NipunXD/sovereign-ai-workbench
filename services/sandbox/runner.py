#!/usr/bin/env python3
"""In-container harness for generated code.

Reads a job description from /workspace/in/job.json, executes the code, and
writes a structured result to stdout. Everything it produces goes to
/workspace/out, which is the only writable mount.

This runs *inside* the sandbox, so it is not a security boundary — the
container is. Its job is to make the result legible: capture stdout and stderr
separately, report the exception type rather than a raw traceback dump, and
list what the code actually wrote.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import resource
import sys
import traceback
from pathlib import Path

IN_DIR = Path("/workspace/in")
OUT_DIR = Path("/workspace/out")

#: Output caps. A runaway print loop must not produce a gigabyte of stdout that
#: then has to be shipped back and stored.
MAX_STREAM_CHARS = 256_000
MAX_OUTPUT_FILES = 32
MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def apply_limits(job: dict) -> None:
    """Belt-and-braces limits inside the container.

    Docker already caps memory, CPU and process count. These add a file-size
    cap and turn an out-of-memory condition into a Python MemoryError, which
    produces a readable message instead of the process being killed with no
    explanation.
    """
    with contextlib.suppress(ValueError, OSError):
        max_file = int(job.get("max_file_bytes", 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file, max_file))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def collect_outputs() -> list[dict]:
    """Describe what the code wrote, without reading it all into memory."""
    files: list[dict] = []
    total = 0
    for path in sorted(OUT_DIR.rglob("*")):
        if not path.is_file():
            continue
        if len(files) >= MAX_OUTPUT_FILES:
            break
        size = path.stat().st_size
        total += size
        if total > MAX_OUTPUT_BYTES:
            break
        files.append(
            {
                "name": str(path.relative_to(OUT_DIR)),
                "size_bytes": size,
                "suffix": path.suffix.lower(),
            }
        )
    return files


def main() -> int:
    try:
        job = json.loads((IN_DIR / "job.json").read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"unreadable job: {exc}"}))
        return 2

    apply_limits(job)
    code = str(job.get("code", ""))

    stdout, stderr = io.StringIO(), io.StringIO()
    status = "ok"
    error: str | None = None

    # The code sees /workspace/out as its working directory, so a bare
    # open("chart.png", "w") lands somewhere the caller can collect it.
    os.chdir(OUT_DIR)

    namespace: dict = {
        "__name__": "__main__",
        "IN_DIR": str(IN_DIR),
        "OUT_DIR": str(OUT_DIR),
    }

    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(code, "<generated>", "exec"), namespace)
    except MemoryError:
        status, error = "memory", "the code exceeded the memory limit"
    except SystemExit as exc:
        if exc.code not in (0, None):
            status, error = "error", f"the code called sys.exit({exc.code})"
    except BaseException as exc:
        status = "error"
        # The last frame is what the author needs; the harness frames above it
        # are noise.
        frames = traceback.format_exception(type(exc), exc, exc.__traceback__)
        error = f"{type(exc).__name__}: {exc}"
        stderr.write("".join(frames[-3:]))

    print(
        json.dumps(
            {
                "status": status,
                "error": error,
                "stdout": stdout.getvalue()[:MAX_STREAM_CHARS],
                "stderr": stderr.getvalue()[:MAX_STREAM_CHARS],
                "output_files": collect_outputs(),
            }
        ),
        file=sys.__stdout__,
    )
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
