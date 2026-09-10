"""Rendering eval results.

Two audiences. On a terminal an engineer wants to see immediately which
threshold moved; in a file the same run has to be diffable against last week's,
which is what makes a regression visible at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness.registry import SuiteResult

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


@dataclass
class Comparison:
    metric: str
    current: float
    baseline: float | None

    @property
    def delta(self) -> float | None:
        return None if self.baseline is None else self.current - self.baseline

    def arrow(self, higher_is_better: bool) -> str:
        if self.delta is None or abs(self.delta) < 1e-9:
            return "   ="
        improved = (self.delta > 0) == higher_is_better
        return f"{GREEN if improved else RED}{'▲' if self.delta > 0 else '▼'}{abs(self.delta):.3g}{RESET}"


#: Metrics where a smaller number is better. Everything else is treated as
#: higher-is-better, which is the common case.
LOWER_IS_BETTER = {
    "cer",
    "wer",
    "cer_no_spaces",
    "decide_p50_ms",
    "decide_p95_ms",
    "unsupported_rate",
    "hallucinated_citation_rate",
    "p50_s",
    "p95_s",
    "mean_s",
}


def render_terminal(results: list[SuiteResult], baselines: dict[str, dict[str, float]]) -> str:
    lines: list[str] = []
    lines.append(f"\n{BOLD}Sovereign AI Workbench — evaluation{RESET}")
    lines.append("=" * 74)

    for result in results:
        status = (
            f"{YELLOW}SKIP{RESET}"
            if result.skipped
            else f"{RED}ERROR{RESET}"
            if result.error
            else f"{GREEN}PASS{RESET}"
            if result.passed
            else f"{RED}FAIL{RESET}"
        )
        lines.append(
            f"\n{BOLD}{result.suite}{RESET}  {status}  {DIM}{result.duration_s:.1f}s{RESET}"
        )

        if result.skipped:
            lines.append(f"  {DIM}{result.skipped}{RESET}")
            continue
        if result.error:
            lines.append(f"  {RED}{result.error}{RESET}")
            continue

        baseline = baselines.get(result.suite, {})
        for name, value in result.metrics.items():
            comparison = Comparison(name, value, baseline.get(name))
            higher_is_better = name not in LOWER_IS_BETTER
            threshold = result.thresholds.get(name)
            mark = ""
            if threshold:
                mark = (
                    f"  {GREEN}ok{RESET}"
                    if result.meets(name)
                    else f"  {RED}below {threshold[0]} {threshold[1]:g}{RESET}"
                )
            lines.append(
                f"  {name:<28} {value:>10.4g}  {comparison.arrow(higher_is_better):<18}{mark}"
            )

        failed = [c for c in result.cases if not c.passed]
        if failed:
            lines.append(f"  {DIM}{len(failed)} of {len(result.cases)} cases failed{RESET}")
            for case in failed[:3]:
                lines.append(f"    {DIM}· {case.case_id}: {case.detail[:70]}{RESET}")

    lines.append("\n" + "=" * 74)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    total = sum(1 for r in results if not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    summary = f"{passed}/{total} suites passed"
    if skipped:
        summary += f", {skipped} skipped"
    lines.append(f"{BOLD}{summary}{RESET}\n")
    return "\n".join(lines)


def write_results(results: list[SuiteResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "results": [r.as_dict() for r in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_baselines(path: Path) -> dict[str, dict[str, float]]:
    """Read the committed baseline, if there is one."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {suite: data.get("metrics", {}) for suite, data in raw.items()}


def write_baselines(results: list[SuiteResult], path: Path) -> None:
    """Record the current numbers as the reference for future runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    for result in results:
        # A skipped suite must not erase the numbers already recorded for it.
        if result.skipped or result.error:
            continue
        existing[result.suite] = {"metrics": result.metrics}
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
