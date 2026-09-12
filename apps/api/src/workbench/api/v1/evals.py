"""The measurement harness, readable from inside the product.

Every claim this system makes about its own accuracy comes from
``evals/suites``, and until now those numbers lived in timestamped JSON on
somebody's laptop. That is the wrong place for them: "how do you know it does
not hallucinate" deserves a screen, not a slide, and the person best placed to
distrust the answer is the engineer using it.

So this reads the harness's own output and serves it. Two deliberate choices:

  - The newest run *per suite*, not the newest file. Suites are run separately —
    the grounding suite takes eleven minutes and needs models loaded, the
    retrieval suite takes two seconds — so the latest file rarely contains all
    four. Taking the newest result for each suite independently is what makes
    the page show the truth rather than whichever subset ran last.
  - Thresholds travel with the metrics. A number without the bar it had to
    clear is trivia; ``recall@10 of 1.00`` means something only next to
    ``>= 0.85``.

Nothing here is computed. If a suite has never been run it is absent, and the
page says so rather than showing a zero.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from workbench.api.deps import CurrentPrincipal
from workbench.core.logging import get_logger
from workbench.settings import Settings, get_settings

router = APIRouter(prefix="/evals", tags=["evals"])
log = get_logger(__name__)

#: What each suite measures, in the words someone would use out loud. The
#: harness stores metric keys, not sentences, and a bare key on screen is only
#: legible to whoever wrote it.
SUITE_BLURB: dict[str, str] = {
    "grounding": "Whether answers are supported by the documents, and whether unanswerable questions are refused rather than guessed at.",
    "retrieval": "Whether the passages that should be found are found, and how far up the ranking they land.",
    "ocr": "How accurately scanned pages are read, against a transcript of what they actually say.",
    "router": "Whether each request reaches a model capable of serving it, and how long choosing costs.",
}

#: Metrics where a smaller number is the better one.
LOWER_IS_BETTER = {"cer", "cer_no_spaces", "wer", "hallucinated_citation_rate", "failed_run_rate"}


class MetricOut(BaseModel):
    key: str
    value: float
    threshold: str | None = None
    passed: bool | None = None
    lower_is_better: bool = False


class SuiteOut(BaseModel):
    suite: str
    blurb: str
    passed: bool
    ran_at: str
    duration_s: float
    cases: int
    failed_cases: list[str]
    metrics: list[MetricOut]
    error: str = ""


class EvalsOut(BaseModel):
    suites: list[SuiteOut]
    #: Suites the harness knows about that have never produced a result.
    never_run: list[str]


def _ran_at(path: Path) -> str:
    """The timestamp in the filename — eval-20260910T191125.json."""
    stamp = path.stem.removeprefix("eval-")
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _newest_per_suite(directory: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    newest: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(directory.glob("eval-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # One corrupt file must not take the page down with it.
            log.warning("eval_result_unreadable", path=str(path), error=str(exc))
            continue
        for result in payload.get("results", []):
            suite = result.get("suite")
            if suite:
                newest[suite] = (path, result)
    return newest


def _metrics(result: dict[str, Any]) -> list[MetricOut]:
    thresholds: dict[str, list[Any]] = result.get("thresholds", {}) or {}
    out: list[MetricOut] = []
    for key, value in sorted((result.get("metrics") or {}).items()):
        if key == "cases":
            continue
        bound = thresholds.get(key)
        passed: bool | None = None
        text: str | None = None
        if bound and len(bound) == 2:
            operator, limit = bound[0], float(bound[1])
            text = f"{operator} {limit:g}"
            passed = value >= limit if operator.startswith(">") else value <= limit
        out.append(
            MetricOut(
                key=key,
                value=float(value),
                threshold=text,
                passed=passed,
                lower_is_better=key in LOWER_IS_BETTER,
            )
        )
    return out


@router.get("", response_model=EvalsOut)
async def latest_results(
    principal: CurrentPrincipal,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvalsOut:
    """The newest result for each eval suite."""
    directory = settings.eval_results_dir
    if not await anyio.to_thread.run_sync(directory.is_dir):
        return EvalsOut(suites=[], never_run=sorted(SUITE_BLURB))

    newest = await anyio.to_thread.run_sync(_newest_per_suite, directory)

    suites = [
        SuiteOut(
            suite=name,
            blurb=SUITE_BLURB.get(name, ""),
            passed=bool(result.get("passed")),
            ran_at=_ran_at(path),
            duration_s=float(result.get("duration_s") or 0.0),
            cases=int((result.get("metrics") or {}).get("cases") or len(result.get("cases") or [])),
            failed_cases=[
                str(case.get("case_id"))
                for case in (result.get("cases") or [])
                if not case.get("passed")
            ],
            metrics=_metrics(result),
            error=str(result.get("error") or ""),
        )
        for name, (path, result) in sorted(newest.items())
    ]
    return EvalsOut(
        suites=suites,
        never_run=sorted(set(SUITE_BLURB) - set(newest)),
    )
