"""Suite registration and the result shapes every suite returns."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseResult:
    """One labelled case."""

    case_id: str
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    detail: str = ""
    #: Kept for failures so a regression can be diagnosed without re-running.
    expected: Any = None
    actual: Any = None


@dataclass
class SuiteResult:
    suite: str
    #: Headline numbers, the ones compared against a baseline.
    metrics: dict[str, float] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)
    #: Thresholds the suite asserts, as metric -> (comparison, value).
    #: comparison is ">=" or "<=".
    thresholds: dict[str, tuple[str, float]] = field(default_factory=dict)
    duration_s: float = 0.0
    skipped: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        if self.skipped or self.error:
            return not self.error
        return all(self.meets(name) for name in self.thresholds)

    def meets(self, metric: str) -> bool:
        if metric not in self.thresholds or metric not in self.metrics:
            return True
        comparison, target = self.thresholds[metric]
        value = self.metrics[metric]
        return value >= target if comparison == ">=" else value <= target

    @property
    def failures(self) -> list[str]:
        out = []
        for name, (comparison, target) in self.thresholds.items():
            if name in self.metrics and not self.meets(name):
                out.append(f"{name} {self.metrics[name]:.4g} (needs {comparison} {target:g})")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "metrics": self.metrics,
            "thresholds": {k: list(v) for k, v in self.thresholds.items()},
            "duration_s": round(self.duration_s, 2),
            "passed": self.passed,
            "skipped": self.skipped,
            "error": self.error,
            "cases": [self._case_dict(c) for c in self.cases],
        }

    @staticmethod
    def _case_dict(case: CaseResult) -> dict[str, Any]:
        """Serialise one case, keeping the evidence for the ones that failed.

        ``expected``/``actual`` are recorded only on failures. They are the
        whole point of the fields — the grounding suite takes twelve minutes,
        and a saved result that says "answered a question the corpus cannot
        support" without the answer forces a re-run to learn anything. Keeping
        them on passing cases too would bloat the file with text nobody reads.
        """
        out: dict[str, Any] = {
            "case_id": case.case_id,
            "passed": case.passed,
            "metrics": case.metrics,
            "detail": case.detail,
        }
        if not case.passed:
            if case.expected is not None:
                out["expected"] = case.expected
            if case.actual is not None:
                out["actual"] = case.actual
        return out


SuiteFn = Callable[[], Awaitable[SuiteResult]]
_SUITES: dict[str, SuiteFn] = {}


def suite(name: str) -> Callable[[SuiteFn], SuiteFn]:
    """Register an eval suite under a name usable with `make eval-<name>`."""

    def decorator(fn: SuiteFn) -> SuiteFn:
        _SUITES[name] = fn
        return fn

    return decorator


def registered() -> dict[str, SuiteFn]:
    # Importing the package registers every suite via the decorator.
    import evals.suites  # noqa: F401

    return dict(_SUITES)


async def run_suite(name: str) -> SuiteResult:
    """Run one suite, converting a crash into a reported failure.

    A suite that raises should not take down the whole run: the other numbers
    are still worth having, and "this suite errored" is itself a result.
    """
    suites = registered()
    if name not in suites:
        return SuiteResult(suite=name, error=f"no suite named '{name}'")

    started = time.perf_counter()
    try:
        result = await suites[name]()
    except Exception as exc:
        import traceback

        return SuiteResult(
            suite=name,
            error=f"{type(exc).__name__}: {exc}",
            duration_s=time.perf_counter() - started,
            cases=[
                CaseResult(case_id="<crash>", passed=False, detail=traceback.format_exc()[-800:])
            ],
        )
    result.duration_s = time.perf_counter() - started
    return result
