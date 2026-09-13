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

#: The gold set each suite is scored against. A failing case is only useful if
#: you can see the question it was, and that lives in the dataset rather than
#: in the result — the harness stores the verdict, not the prompt.
SUITE_DATASET: dict[str, str] = {
    "grounding": "grounding.jsonl",
    "retrieval": "retrieval_qa.jsonl",
    "router": "router_labels.jsonl",
}

#: Metrics where a smaller number is the better one.
LOWER_IS_BETTER = {"cer", "cer_no_spaces", "wer", "hallucinated_citation_rate", "failed_run_rate"}


class CaseOut(BaseModel):
    """A case that did not pass, with enough context to judge it."""

    case_id: str
    #: The question as it appears in the gold set.
    prompt: str = ""
    #: What the case was supposed to do, in a sentence.
    expectation: str = ""
    #: The harness's verdict — why this counted as a failure.
    detail: str = ""
    #: What the system actually produced, trimmed.
    actual: str = ""
    #: Where the case is defined, so it can be found and changed.
    source: str = ""
    metrics: dict[str, float] = {}


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
    failures: list[CaseOut] = []
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


def _gold_set(directory: Path, filename: str) -> dict[str, dict[str, Any]]:
    """The dataset rows for a suite, keyed by case id."""
    path = directory / filename
    rows: dict[str, dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("eval_dataset_unreadable", path=str(path), error=str(exc))
    return rows


def _expectation(row: dict[str, Any]) -> str:
    """What the case was supposed to do, said in one sentence.

    Built from whichever keys the dataset uses rather than a per-suite branch:
    the gold sets share an id and a prompt and otherwise describe themselves.
    """
    if "answerable" in row:
        if not row["answerable"]:
            note = str(row.get("note") or "").strip()
            return (
                f"Should refuse — {note}" if note else "Should refuse: the corpus cannot support it"
            )
        phrase = str(row.get("expect_phrase") or "").strip()
        return (
            f'Should answer, mentioning "{phrase}"' if phrase else "Should answer from the corpus"
        )
    if "expect_doc" in row or "expect_phrase" in row:
        doc = str(row.get("expect_doc") or "").strip()
        phrase = str(row.get("expect_phrase") or "").strip()
        parts = [f"Should retrieve {doc}" if doc else "Should retrieve the right passage"]
        if phrase:
            parts.append(f'containing "{phrase}"')
        return ", ".join(parts)
    if "lane" in row:
        return f"Should route to the {row['lane']} lane"
    return str(row.get("note") or "")


def _failures(
    result: dict[str, Any], gold: dict[str, dict[str, Any]], source: str
) -> list[CaseOut]:
    out: list[CaseOut] = []
    for case in result.get("cases") or []:
        if case.get("passed"):
            continue
        case_id = str(case.get("case_id") or "")
        row = gold.get(case_id, {})
        out.append(
            CaseOut(
                case_id=case_id,
                prompt=str(row.get("query") or row.get("text") or ""),
                expectation=_expectation(row),
                detail=str(case.get("detail") or ""),
                actual=str(case.get("actual") or "")[:600],
                source=f"{source}:{case_id}" if source else "",
                metrics={
                    k: float(v)
                    for k, v in (case.get("metrics") or {}).items()
                    if isinstance(v, int | float)
                },
            )
        )
    return out


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

    gold: dict[str, dict[str, dict[str, Any]]] = {}
    for name in newest:
        filename = SUITE_DATASET.get(name)
        if filename:
            gold[name] = await anyio.to_thread.run_sync(
                _gold_set, settings.eval_datasets_dir, filename
            )

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
            failures=_failures(
                result,
                gold.get(name, {}),
                f"evals/datasets/{SUITE_DATASET[name]}" if name in SUITE_DATASET else "",
            ),
            error=str(result.get("error") or ""),
        )
        for name, (path, result) in sorted(newest.items())
    ]
    return EvalsOut(
        suites=suites,
        never_run=sorted(set(SUITE_BLURB) - set(newest)),
    )
