#!/usr/bin/env python3
"""Run the eval suites.

    make eval                  every suite
    make eval-retrieval        one suite
    make eval -- --update-baseline    record the current numbers as the reference

A suite that cannot run is skipped with a reason rather than failing, so the
fast suites still report on a machine with no models installed. A suite that
*crashes* is a failure — that is a defect, not an absence.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from evals.harness.registry import SuiteResult, registered, run_suite  # noqa: E402
from evals.harness.report import (  # noqa: E402
    load_baselines,
    render_terminal,
    write_baselines,
    write_results,
)

RESULTS_DIR = REPO_ROOT / "evals" / "results"
BASELINE_PATH = RESULTS_DIR / "baselines" / "baseline.json"

#: Cheap suites first, so a broken retrieval index is visible in seconds rather
#: than after the grounding suite has spent ten minutes talking to a model.
ORDER = ["router", "ocr", "retrieval", "grounding"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="run every suite")
    parser.add_argument("--suite", action="append", help="run one suite (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the available suites")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="record these numbers as the reference for future runs",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip suites that call a model (router, ocr and retrieval only)",
    )
    parser.add_argument("--json", type=Path, help="also write results to this path")
    args = parser.parse_args()

    available = registered()
    if args.list:
        for name in ORDER:
            if name in available:
                print(name)
        return 0

    selected = args.suite or ORDER
    if args.fast:
        selected = [name for name in selected if name != "grounding"]

    unknown = [name for name in selected if name not in available]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(sorted(available))}", file=sys.stderr)
        return 2

    results: list[SuiteResult] = []
    for name in selected:
        print(f"running {name}…", file=sys.stderr)
        results.append(await run_suite(name))

    baselines = load_baselines(BASELINE_PATH)
    print(render_terminal(results, baselines))

    from workbench.core.clock import now

    stamp = now().strftime("%Y%m%dT%H%M%S")
    write_results(results, args.json or (RESULTS_DIR / f"eval-{stamp}.json"))

    if args.update_baseline:
        write_baselines(results, BASELINE_PATH)
        print(f"baseline updated: {BASELINE_PATH.relative_to(REPO_ROOT)}\n")

    # A skipped suite is not a failure — the machine simply cannot run it.
    failed = [r for r in results if not r.passed and not r.skipped]
    for result in failed:
        for failure in result.failures:
            print(f"  {result.suite}: {failure}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
