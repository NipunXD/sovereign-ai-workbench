"""Extra documents for demonstrating the workbench in front of people.

These are deliberately *not* part of the seed corpus. The seed corpus is what
the system already knows; these are what you ingest while someone is watching,
so that "the knowledge base is yours to extend" is something they see happen
rather than something they are told.

Four files, each chosen to exercise a path the seed corpus does not:

  1. INSP-2030-V1202.pdf      native text, about a vessel nothing else covers,
                              so the same question refuses before it is
                              ingested and answers after.
  2. WP-2030-0912-scanned.pdf a third-generation photocopy. Mean OCR confidence
                              lands at ~0.69, under the 0.72 escalation
                              threshold in config/ingest.yaml, so the vision
                              model re-reads the page and its blocks come back
                              marked source='vlm'. Tuned deliberately to stay
                              *just* under the line rather than far under it:
                              OCR still recovers ~88 words, so if the vision
                              model is slow or unavailable the document is
                              still a good one, merely flagged. A demo file
                              that only works when everything works is a demo
                              file that fails in front of people.
  3. P-101A-VIBRATION.csv     a spreadsheet, for the calculation beat and for
                              the transcript view the document viewer falls
                              back to when a source has no page to render.
  4. NOTE-2030-V1202.txt      plain text, the cheapest possible ingest, and a
                              second opinion that disagrees with (1) — which
                              is what makes "what did it read" worth asking.

    python scripts/demo_corpus.py            # writes data/demo/
    python scripts/demo_corpus.py --check    # also dry-runs the ingester
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "data/seed/generator")

OUT = Path("data/demo")


# --------------------------------------------------------------------------
# 1 + 2: documents rendered the same way the seed corpus is
# --------------------------------------------------------------------------
def documents():
    from content import Section, SeedDocument

    inspection = SeedDocument(
        doc_id="INSP-2030-V1202",
        title="Inspection Report V-1202 — Shell Thickness Survey January 2030",
        doc_type="inspection",
        classification="confidential",
        departments=["inspection"],
        tags=["V-1202", "UT", "corrosion", "reflux accumulator"],
        sections=[
            Section(
                "1. Equipment Details",
                1,
                "Equipment: V-1202 Reflux Accumulator. Design pressure 12.5 barg. "
                "Design temperature 150 degC. Material SA-516 Gr.70. Nominal shell "
                "thickness 12.0 mm. Minimum allowable thickness 7.50 mm. "
                "Previous inspection 2028-11-20.",
            ),
            Section(
                "2. Thickness Survey",
                1,
                "Ultrasonic measurements were taken at six condition monitoring "
                "locations on 2030-01-22 using a calibrated DM5E gauge. Surface "
                "temperature 31 degC. All readings are the minimum of five shots "
                "at each location.",
                table=[
                    ["CML", "Location", "2028 (mm)", "2030 (mm)", "t-min (mm)"],
                    ["CML-01", "Top head", "11.90", "11.75", "7.50"],
                    ["CML-02", "Shell course 1", "11.85", "11.40", "7.50"],
                    ["CML-03", "Shell course 2", "11.70", "10.95", "7.50"],
                    ["CML-04", "Bottom shell", "11.40", "9.85", "7.50"],
                    ["CML-05", "Outlet nozzle N2", "11.20", "9.10", "7.50"],
                    ["CML-06", "Boot", "10.80", "8.35", "7.50"],
                ],
            ),
            Section(
                "3. Findings",
                1,
                "Wall loss is concentrated in the boot and the outlet nozzle, which "
                "is consistent with water-phase corrosion at the liquid interface. "
                "CML-06 has lost 2.45 mm since 2028 and is the governing location. "
                "No through-wall defect was detected. External coating is intact.",
            ),
            Section(
                "4. Recommendation",
                1,
                "Re-inspect CML-06 and CML-05 at twelve-month intervals rather than "
                "the standard thirty-six. Boot drainage should be reviewed during "
                "the 2031 turnaround. Next full survey due 2031-01.",
            ),
            Section(
                "5. Sign-off",
                1,
                "Inspected by: S. Nair, Senior Inspection Engineer. "
                "Reviewed by: M. Devadiga, Maintenance Head. Date: 2030-01-24.",
            ),
        ],
    )

    permit = SeedDocument(
        doc_id="WP-2030-0912",
        title="MRPL WORK PERMIT WP-2030-0912",
        doc_type="inspection",
        classification="internal",
        departments=["operations", "inspection"],
        tags=["V-1202", "permit", "hot work"],
        sections=[
            Section(
                "1. Scope",
                1,
                "Hot work permit for welding repair on the boot drain nozzle of "
                "vessel V-1202 in the Crude Distillation Unit. Issued 2030-02-11, "
                "valid for one shift only.",
            ),
            Section(
                "2. Isolation",
                1,
                "Vessel isolated by HV-1212 and XV-1214, both car-sealed closed. "
                "Confirmed zero flow on FI-1212. Vessel purged with nitrogen to "
                "below 1 percent LEL. Purge duration 45 minutes at 3 barg.",
            ),
            Section(
                "3. Gas Test",
                1,
                "Initial gas test 0.3 percent LEL at 07:20. Oxygen 20.9 percent. "
                "Retest required every two hours. Hydrogen sulphide not detected.",
            ),
            Section(
                "4. Authorisation",
                1,
                "Issued by: S. Nair, Senior Inspection Engineer. "
                "Approved by: M. Devadiga, Maintenance Head. "
                "Fire watch: R. Kamath.",
            ),
        ],
        scan_profile="thirdgen",
    )

    return inspection, permit


#: Damage tuned against the real ingester, not guessed. The stock "fax" profile
#: in data/seed/generator leaves confidence at 0.90 — comfortably accepted — and
#: the eval baselines depend on it, so this lives here instead of there.
#: Measured: mean confidence 0.694, 88 words recovered, escalation reason
#: "mean confidence 69% below 72%".
THIRD_GENERATION = {
    "skew_degrees": 2.0,
    "blur_kernel": 7,
    "speckle": 0.045,
    "jpeg_quality": 34,
    "brightness": 0.88,
    "contrast": 1.45,
    "gradient": 0.30,
    "target_dpi": 150,
}


def write_pdfs() -> list[Path]:
    from degrade import ScanProfile, pdf_to_scanned_pdf
    from render import render_pdf

    thirdgen = ScanProfile("thirdgen", **THIRD_GENERATION)

    written = []
    for document in documents():
        target = OUT / f"{document.doc_id}.pdf"
        if document.scan_profile:
            native = OUT / f".{document.doc_id}-native.pdf"
            render_pdf(document, native)
            target = OUT / f"{document.doc_id}-scanned.pdf"
            pdf_to_scanned_pdf(native, target, thirdgen, seed=7)
            native.unlink()
        else:
            render_pdf(document, target)
        written.append(target)
    return written


# --------------------------------------------------------------------------
# 3: a spreadsheet, for the calculation beat
# --------------------------------------------------------------------------
#: Monthly overall vibration velocity on the crude charge pump. The trend is
#: linear on purpose: the agent has to compute a rate and a crossing date, and
#: an answer that is merely plausible is visibly wrong against these numbers.
VIBRATION = [
    ["Date", "Tag", "Point", "Velocity_mm_s_rms", "Bearing_temp_C"],
    ["2029-01-15", "P-101A", "Motor DE", "2.8", "52"],
    ["2029-02-15", "P-101A", "Motor DE", "3.1", "53"],
    ["2029-03-15", "P-101A", "Motor DE", "3.4", "54"],
    ["2029-04-15", "P-101A", "Motor DE", "3.6", "55"],
    ["2029-05-15", "P-101A", "Motor DE", "4.0", "57"],
    ["2029-06-15", "P-101A", "Motor DE", "4.3", "58"],
    ["2029-07-15", "P-101A", "Motor DE", "4.7", "60"],
    ["2029-08-15", "P-101A", "Motor DE", "5.0", "61"],
    ["2029-09-15", "P-101A", "Motor DE", "5.4", "63"],
    ["2029-10-15", "P-101A", "Motor DE", "5.7", "64"],
    ["2029-11-15", "P-101A", "Motor DE", "6.1", "66"],
    ["2029-12-15", "P-101A", "Motor DE", "6.4", "67"],
]


def write_csv() -> Path:
    target = OUT / "P-101A-VIBRATION-2029.csv"
    with target.open("w", newline="") as handle:
        csv.writer(handle).writerows(VIBRATION)
    return target


# --------------------------------------------------------------------------
# 4: plain text that disagrees with the inspection report
# --------------------------------------------------------------------------
NOTE = """FIELD NOTE — V-1202 BOOT CORROSION
Ref: INSP-2030-V1202
Author: A. Pinto, Corrosion Engineer
Date: 2030-02-02

The January survey records 8.35 mm at CML-06 against a t-min of 7.50 mm.
I do not agree with the twelve-month re-inspection interval proposed in
section 4 of that report.

Rate of loss at CML-06 is 2.45 mm over 14 months, which is 2.10 mm/year.
At that rate the remaining margin of 0.85 mm is consumed in approximately
five months, not twelve. The interval should be set at four months with a
hold point before the 2031 turnaround.

This note has not been countersigned by the Inspection Head.
"""


def write_note() -> Path:
    target = OUT / "NOTE-2030-V1202.txt"
    target.write_text(NOTE)
    return target


# --------------------------------------------------------------------------
# dry run: what the ingester will make of each file, without indexing it
# --------------------------------------------------------------------------
async def check(paths: list[Path], *, vision: bool) -> None:
    """Run each file through the real ingester without indexing it.

    Built the way scripts/seed_corpus.py builds it, so what this reports is
    what the portal will do — in particular whether the faxed permit falls
    under the escalation threshold and gets re-read by the vision model.
    """
    from workbench.ingest.ocr.engine import build_engine
    from workbench.ingest.pipeline import IngestionPipeline, PipelineConfig
    from workbench.ingest.storage import BlobStore
    from workbench.ingest.vision.reader import VisionReader
    from workbench.providers.registry import ModelRegistry
    from workbench.providers.residency import ResidencyManager
    from workbench.rag.chunker import Chunker, ChunkSpec
    from workbench.settings import get_settings

    settings = get_settings()
    registry = ModelRegistry(settings.models_manifest)
    await registry.probe_availability()
    residency = ResidencyManager(registry, max_resident_gb=14.0)

    pipeline = IngestionPipeline(
        blob_store=BlobStore(settings.blob_dir),
        page_image_dir=settings.page_image_dir,
        dataset_dir=settings.data_dir / "datasets",
        chunker=Chunker(ChunkSpec()),
        ocr_engine=build_engine("rapidocr"),
        vision_reader=VisionReader(registry=registry, residency=residency) if vision else None,
        config=PipelineConfig(vision_enabled=vision),
    )

    print(f"\n{'file':<32} {'pages':>5} {'chunks':>6} {'conf':>6} {'ocr':>4} {'vlm':>4}  sources")
    print("-" * 82)
    for path in paths:
        try:
            result = await pipeline.run(
                data=path.read_bytes(), filename=path.name, doc_id=f"probe_{path.stem}"
            )
        except Exception as exc:
            # A file that cannot be ingested is a bad demo file. Say so here
            # rather than in front of an audience.
            print(f"{path.name:<32} FAILED — {exc.__class__.__name__}: {exc}")
            continue
        report = result.ir.extraction_report
        sources = sorted({b.source for p in result.ir.pages for b in p.blocks})
        print(
            f"{path.name:<32} {result.ir.page_count:>5} {len(result.chunks):>6} "
            f"{result.ir.mean_confidence:>6.3f} {report.pages_ocr:>4} "
            f"{report.pages_vlm_escalated:>4}  {','.join(sources)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="dry-run the ingester too")
    parser.add_argument("--no-vision", action="store_true", help="skip VLM escalation (faster)")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    paths = [*write_pdfs(), write_csv(), write_note()]
    for path in paths:
        print(f"  {path}  ({path.stat().st_size // 1024} KB)")
    if args.check:
        asyncio.run(check(paths, vision=not args.no_vision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
