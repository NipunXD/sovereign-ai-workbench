"""OCR accuracy against the corpus generator's own source text.

The seed corpus is rendered from known strings and then degraded through
simulated scanning, so ground truth is exact and costs nothing — no hand
transcription, and no drift between the labels and the documents.

Two error rates are reported, and the gap between them is the point. Plain CER
counts a lost word boundary as several errors; the space-insensitive figure
isolates whether the characters were recognised at all. On this corpus the two
were 16% and 3%, which is what showed the problem was tokenisation rather than
the recogniser.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.metrics import (
    WORD_FAULTS,
    character_error_rate,
    character_error_rate_ignoring_spaces,
    word_error_composition,
    word_error_rate,
)
from evals.harness.registry import CaseResult, SuiteResult, suite

REPO_ROOT = Path(__file__).resolve().parents[2]
TRUTH_DIR = REPO_ROOT / "data" / "seed" / "ground_truth"
CORPUS_DIR = REPO_ROOT / "data" / "seed" / "corpus"


@suite("ocr")
async def run() -> SuiteResult:
    import asyncio

    manifest_path = TRUTH_DIR / "doc_manifest.json"
    truth_path = TRUTH_DIR / "ocr_pages.jsonl"
    if not manifest_path.is_file() or not truth_path.is_file():
        return SuiteResult(
            suite="ocr", skipped="the corpus has not been generated — run `make seed-corpus`"
        )

    manifest = json.loads(manifest_path.read_text())
    truth = {
        json.loads(line)["doc_id"]: json.loads(line)["text"]
        for line in truth_path.read_text().splitlines()
        if line.strip()
    }

    scanned = [
        entry
        for entry in manifest
        if entry["scan_profile"] != "native" and entry["path"].endswith(".pdf")
    ]
    if not scanned:
        return SuiteResult(suite="ocr", skipped="no scanned documents in the corpus")

    try:
        from workbench.ingest.extractors.pdf import PdfExtractor
        from workbench.ingest.ocr.engine import build_engine
        from workbench.ingest.ocr.preprocess import PreprocessOptions, decode, preprocess

        engine = build_engine("rapidocr")
    except Exception as exc:
        return SuiteResult(suite="ocr", skipped=f"OCR unavailable: {exc}")

    dpi = 400
    results: list[CaseResult] = []
    cers: list[float] = []
    cers_no_spaces: list[float] = []
    wers: list[float] = []
    confidences: list[float] = []
    escalations = 0
    #: Summed across documents rather than averaged per document, so a long
    #: page counts for more than a short one — which is what a reader assumes
    #: "12% of words" means.
    faults = dict.fromkeys((*WORD_FAULTS, "words"), 0)

    for entry in scanned:
        expected = truth.get(entry["doc_id"], "")
        if not expected:
            continue
        path = CORPUS_DIR / entry["path"]

        raw = await asyncio.to_thread(PdfExtractor(ocr_dpi=dpi).render_for_ocr, path, 1)
        image = await asyncio.to_thread(decode, raw)
        prepared = await asyncio.to_thread(preprocess, image, PreprocessOptions(source_dpi=dpi))
        recognised = await asyncio.to_thread(engine.recognise, prepared.image)

        cer = character_error_rate(expected, recognised.text)
        cer_ns = character_error_rate_ignoring_spaces(expected, recognised.text)
        wer = word_error_rate(expected, recognised.text)
        for bucket, count in word_error_composition(expected, recognised.text).items():
            faults[bucket] += count
        cers.append(cer)
        cers_no_spaces.append(cer_ns)
        wers.append(wer)
        confidences.append(recognised.mean_confidence)

        from workbench.ingest.ocr.quality import assess

        if assess(recognised).escalate:
            escalations += 1

        # 12% is the plan's target for a degraded scan.
        results.append(
            CaseResult(
                case_id=f"{entry['doc_id']}[{entry['scan_profile']}]",
                passed=cer <= 0.12,
                metrics={"cer": cer, "cer_no_spaces": cer_ns, "wer": wer},
                detail=f"CER {cer:.1%} ({cer_ns:.1%} ignoring spaces) on a {entry['scan_profile']} scan",
            )
        )

    if not results:
        return SuiteResult(suite="ocr", skipped="no ground truth matched the corpus")

    count = len(results)
    return SuiteResult(
        suite="ocr",
        cases=results,
        metrics={
            "cer": sum(cers) / count,
            "cer_no_spaces": sum(cers_no_spaces) / count,
            "wer": sum(wers) / count,
            "mean_confidence": sum(confidences) / count,
            "escalation_rate": escalations / count,
            "render_dpi": float(dpi),
            "cases": float(count),
            # What the wrong words are wrong about, as shares of the document.
            # Reported because the headline WER is unreadable without it: most
            # of those words have identical letters and differ only in a space
            # or a full stop.
            **{
                f"wer_share_{bucket}": (
                    faults[bucket] / faults["words"] if faults["words"] else 0.0
                )
                for bucket in WORD_FAULTS
            },
        },
        thresholds={"cer": ("<=", 0.12), "cer_no_spaces": ("<=", 0.05)},
    )
