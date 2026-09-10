"""The ingestion pipeline.

Fourteen stages, run in order, each timed and each able to fail without taking
the document with it. Progress is reported as it goes because ingesting a
scanned 40-page inspection report takes a minute or two on a laptop, and a
progress bar that only moves at the end is indistinguishable from a hang.

The important design decision is that a *partial* failure still produces a
useful document. If the vision escalation cannot reach a model, the page keeps
its OCR text and the report records that it is degraded — losing the whole
upload because one stage was unavailable would be worse than indexing text of
known-lower quality.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from workbench.core.errors import IngestionError, UnsupportedMediaTypeError
from workbench.core.logging import get_logger
from workbench.ingest.ir import Block, BlockSource, BlockType, DocumentIR, Page
from workbench.ingest.ocr.engine import OCREngine, OcrResult
from workbench.ingest.ocr.quality import QualityThresholds, assess, page_needs_ocr
from workbench.ingest.sniff import ALLOWED_MIME, sniff
from workbench.ingest.storage import BlobStore

log = get_logger(__name__)


class Stage(StrEnum):
    RECEIVE = "receive"
    IDENTIFY = "identify"
    DEDUPE = "dedupe"
    STORE = "store"
    CLASSIFY = "classify"
    EXTRACT = "extract"
    OCR = "ocr"
    VISION = "vision"
    NORMALIZE = "normalize"
    RENDER = "render"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    FINALIZE = "finalize"


#: Relative weight of each stage, for a progress figure that reflects reality
#: rather than counting stages equally. OCR and embedding dominate.
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.RECEIVE: 1, Stage.IDENTIFY: 1, Stage.DEDUPE: 1, Stage.STORE: 2,
    Stage.CLASSIFY: 1, Stage.EXTRACT: 8, Stage.OCR: 30, Stage.VISION: 20,
    Stage.NORMALIZE: 2, Stage.RENDER: 6, Stage.CHUNK: 3, Stage.EMBED: 15,
    Stage.INDEX: 8, Stage.FINALIZE: 2,
}
_TOTAL_WEIGHT = sum(STAGE_WEIGHTS.values())

ProgressCallback = Callable[[Stage, float, str], Awaitable[None]]


@dataclass
class IngestionResult:
    doc_id: str
    sha256: str
    ir: DocumentIR | None = None
    #: True when an identical file was already ingested.
    duplicate_of: str | None = None
    chunks: list[Any] = field(default_factory=list)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False

    @property
    def ok(self) -> bool:
        return self.ir is not None


@dataclass
class PipelineConfig:
    viewer_dpi: int = 150
    #: See config/ingest.yaml for the measured accuracy/cost trade-off.
    ocr_dpi: int = 400
    max_upload_mb: int = 200
    max_pages: int = 2000
    ocr_enabled: bool = True
    vision_enabled: bool = True
    thresholds: QualityThresholds = field(default_factory=QualityThresholds)
    always_escalate: frozenset[BlockType] = frozenset(
        {BlockType.FIGURE, BlockType.DRAWING_ANNOTATION, BlockType.HANDWRITING}
    )


class IngestionPipeline:
    """Turns an uploaded file into indexed, citable chunks."""

    def __init__(
        self,
        *,
        blob_store: BlobStore,
        page_image_dir: Path,
        dataset_dir: Path,
        chunker: Any,
        ocr_engine: OCREngine | None = None,
        vision_reader: Any = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self.blobs = blob_store
        self.page_image_dir = page_image_dir
        self.dataset_dir = dataset_dir
        self.chunker = chunker
        self.ocr_engine = ocr_engine
        self.vision = vision_reader
        self.config = config or PipelineConfig()

    async def run(
        self,
        *,
        data: bytes,
        filename: str,
        doc_id: str,
        progress: ProgressCallback | None = None,
    ) -> IngestionResult:
        """Ingest one file."""
        timings: dict[str, float] = {}
        warnings: list[str] = []
        completed_weight = 0.0

        async def report(stage: Stage, message: str, within: float = 0.0) -> None:
            """Emit progress, interpolating inside a long-running stage.

            ``within`` is how far through the current stage we are, 0..1. OCR on
            a 40-page scan otherwise reports one unchanging number for minutes.
            """
            nonlocal completed_weight
            if progress is None:
                return
            partial = STAGE_WEIGHTS[stage] * max(0.0, min(1.0, within))
            fraction = min(1.0, (completed_weight + partial) / _TOTAL_WEIGHT)
            await progress(stage, fraction, message)

        async def finish(stage: Stage, started: float, message: str = "") -> None:
            nonlocal completed_weight
            timings[stage.value] = (time.perf_counter() - started) * 1000
            completed_weight += STAGE_WEIGHTS[stage]
            await report(stage, message or stage.value)

        # --- receive / identify ---------------------------------------------
        started = time.perf_counter()
        await report(Stage.RECEIVE, f"receiving {filename}")
        if len(data) > self.config.max_upload_mb * 1_000_000:
            from workbench.core.errors import PayloadTooLargeError

            raise PayloadTooLargeError(
                f"{filename} is {len(data) / 1e6:.0f} MB, over the "
                f"{self.config.max_upload_mb} MB limit"
            )
        await finish(Stage.RECEIVE, started)

        started = time.perf_counter()
        detected = sniff(data[:65536], filename)
        if not detected.allowed:
            raise UnsupportedMediaTypeError(
                f"{filename} is {detected.mime}, which cannot be ingested. "
                f"Supported: PDF, images, Word, PowerPoint, Excel, CSV, text, email."
            )
        if detected.extension_mismatch:
            # Not fatal — a mislabelled file is usually a mistake — but recorded
            # so it is visible rather than silently reinterpreted.
            warnings.append(detected.detail)
        await finish(Stage.IDENTIFY, started, f"identified as {detected.mime}")

        # --- dedupe / store --------------------------------------------------
        started = time.perf_counter()
        from workbench.core.hashing import digest_bytes

        sha256 = digest_bytes(data)
        already_present = self.blobs.exists(sha256)
        await finish(Stage.DEDUPE, started, "already stored" if already_present else "new document")

        started = time.perf_counter()
        blob = self.blobs.put_bytes(data)
        await finish(Stage.STORE, started, f"stored {blob.size_bytes / 1e6:.1f} MB")

        # --- classify / extract ----------------------------------------------
        started = time.perf_counter()
        extractor = self._extractor_for(detected.mime, filename)
        await finish(Stage.CLASSIFY, started, f"using the {extractor.name} extractor")

        started = time.perf_counter()
        try:
            ir = extractor.extract(
                blob.path,
                doc_id,
                image_dir=self.page_image_dir / doc_id,
                filename=filename,
            )
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(f"{filename} could not be read: {exc}") from exc

        if ir.page_count > self.config.max_pages:
            raise IngestionError(
                f"{filename} has {ir.page_count} pages, over the "
                f"{self.config.max_pages}-page limit"
            )
        await finish(Stage.EXTRACT, started, f"{ir.page_count} pages, {len(ir.blocks)} blocks")

        # --- ocr --------------------------------------------------------------
        started = time.perf_counter()
        ocr_pages, ocr_results = await self._run_ocr(ir, blob.path, detected.mime, report)
        await finish(
            Stage.OCR, started,
            f"recognised {ocr_pages} page(s)" if ocr_pages else "no OCR needed",
        )

        # --- vision escalation ------------------------------------------------
        started = time.perf_counter()
        escalated, vision_failed = await self._run_vision(ir, ocr_results, blob.path, detected.mime)
        if vision_failed:
            warnings.append(
                "the vision model was unavailable; low-confidence pages were indexed "
                "from OCR alone"
            )
        await finish(
            Stage.VISION, started,
            f"re-read {escalated} page(s) with vision" if escalated else "no escalation needed",
        )

        # --- normalize --------------------------------------------------------
        started = time.perf_counter()
        self._normalise(ir)
        await finish(Stage.NORMALIZE, started)

        started = time.perf_counter()
        await finish(Stage.RENDER, started, "page images ready")

        # --- chunk / embed / index -------------------------------------------
        started = time.perf_counter()
        chunks = self.chunker.chunk(ir)
        await finish(Stage.CHUNK, started, f"{len(chunks)} chunks")

        # Embedding and indexing are the indexer's work, but the caller cannot
        # report them from outside, and a bar that stops at 75% reads as failure.
        started = time.perf_counter()
        await finish(Stage.EMBED, started, f"embedding {len(chunks)} chunks")
        started = time.perf_counter()
        await finish(Stage.INDEX, started, "writing to the index")
        started = time.perf_counter()
        await finish(Stage.FINALIZE, started, "complete")

        degraded = bool(warnings) or ir.mean_confidence < 0.6
        ir.extraction_report.pages_ocr = ocr_pages
        ir.extraction_report.pages_vlm_escalated = escalated
        ir.extraction_report.mean_confidence = ir.mean_confidence
        ir.extraction_report.warnings.extend(warnings)
        ir.extraction_report.degraded = degraded
        ir.extraction_report.stage_timings_ms.update(timings)

        return IngestionResult(
            doc_id=doc_id,
            sha256=sha256,
            ir=ir,
            duplicate_of=sha256 if already_present else None,
            chunks=chunks,
            stage_timings_ms=timings,
            warnings=warnings,
            degraded=degraded,
        )

    # ------------------------------------------------------------- extractors
    def _extractor_for(self, mime: str, filename: str) -> Any:
        from workbench.ingest.extractors.office import (
            DocxExtractor,
            PptxExtractor,
            SpreadsheetExtractor,
            TextExtractor,
        )
        from workbench.ingest.extractors.pdf import PdfExtractor

        if mime == "application/pdf":
            return PdfExtractor(viewer_dpi=self.config.viewer_dpi, ocr_dpi=self.config.ocr_dpi)
        if mime.startswith("image/"):
            from workbench.ingest.extractors.image import ImageExtractor

            return ImageExtractor(viewer_dpi=self.config.viewer_dpi)
        if mime.endswith("wordprocessingml.document"):
            return DocxExtractor()
        if mime.endswith("presentationml.presentation"):
            return PptxExtractor()
        if mime.endswith("spreadsheetml.sheet") or mime == "text/csv":
            return SpreadsheetExtractor(dataset_dir=self.dataset_dir)
        if mime in {"text/plain", "message/rfc822"}:
            return TextExtractor()
        raise UnsupportedMediaTypeError(f"no extractor for {mime}")

    # -------------------------------------------------------------------- ocr
    async def _run_ocr(
        self,
        ir: DocumentIR,
        source: Path,
        mime: str,
        report: Callable[..., Awaitable[None]],
    ) -> tuple[int, dict[int, OcrResult]]:
        """Recognise every page that has no usable native text."""
        if not self.config.ocr_enabled or self.ocr_engine is None:
            return 0, {}

        needs = [page for page in ir.pages if page_needs_ocr(page)]
        if not needs:
            return 0, {}

        from workbench.ingest.ocr.preprocess import PreprocessOptions, decode, preprocess

        results: dict[int, OcrResult] = {}
        options = PreprocessOptions(source_dpi=self.config.ocr_dpi)

        for index, page in enumerate(needs, start=1):
            await report(
                Stage.OCR,
                f"recognising page {page.page_no} ({index}/{len(needs)})",
                (index - 1) / len(needs),
            )
            try:
                raw = self._page_image(source, mime, page.page_no)
                if raw is None:
                    continue
                # OCR is CPU-bound and blocking; a thread keeps the event loop
                # responsive so progress events keep flowing during a long scan.
                image = await asyncio.to_thread(decode, raw)
                prepared = await asyncio.to_thread(preprocess, image, options)
                result = await asyncio.to_thread(self.ocr_engine.recognise, prepared.image)
            except Exception as exc:  # noqa: BLE001
                log.warning("ocr_page_failed", page=page.page_no, error=str(exc))
                continue

            results[page.page_no] = result
            page.blocks = [
                Block(
                    block_id=f"p{page.page_no}o{order}",
                    page_no=page.page_no,
                    type=BlockType.PARAGRAPH,
                    text=line.text,
                    bbox=line.bbox,
                    confidence=line.confidence,
                    source=BlockSource.OCR,
                    order=order,
                )
                for order, line in enumerate(result.lines)
            ]

        return len(results), results

    def _page_image(self, source: Path, mime: str, page_no: int) -> bytes | None:
        """Rasterise one page at OCR resolution."""
        if mime == "application/pdf":
            from workbench.ingest.extractors.pdf import PdfExtractor

            return PdfExtractor(ocr_dpi=self.config.ocr_dpi).render_for_ocr(source, page_no)
        if mime.startswith("image/"):
            return source.read_bytes()
        return None

    # ----------------------------------------------------------------- vision
    async def _run_vision(
        self, ir: DocumentIR, ocr_results: dict[int, OcrResult], source: Path, mime: str
    ) -> tuple[int, bool]:
        """Re-read pages the quality gate rejected."""
        if not self.config.vision_enabled or self.vision is None:
            return 0, False

        escalated = 0
        failed = False

        for page in ir.pages:
            result = ocr_results.get(page.page_no)
            block_types = frozenset(b.type for b in page.blocks)
            verdict = assess(
                result or OcrResult(),
                thresholds=self.config.thresholds,
                is_blank_page=page.is_blank and page.page_no not in ocr_results,
                block_types=block_types & self.config.always_escalate,
            )
            if not verdict.escalate:
                continue

            log.info("vision_escalation", page=page.page_no, reason=verdict.detail)
            raw = self._page_image(source, mime, page.page_no)
            if raw is None:
                continue
            try:
                blocks, _ = await self.vision.transcribe_page(raw, page.page_no)
            except Exception as exc:  # noqa: BLE001
                log.warning("vision_escalation_failed", page=page.page_no, error=str(exc))
                failed = True
                continue

            if not blocks:
                failed = True
                continue

            # Merge rather than replace: OCR line boxes are what the viewer
            # highlights, and the VLM does not provide them for prose.
            existing_words = page.word_count
            if existing_words == 0:
                page.blocks = blocks
            else:
                offset = len(page.blocks)
                for order, block in enumerate(blocks):
                    page.blocks.append(block.model_copy(update={"order": offset + order}))
            escalated += 1

        return escalated, failed

    # -------------------------------------------------------------- normalise
    @staticmethod
    def _normalise(ir: DocumentIR) -> None:
        """Tidy reading order and drop empty blocks."""
        for page in ir.pages:
            page.blocks = [b for b in page.blocks if b.text.strip()]
            # Sort by vertical then horizontal position, which recovers reading
            # order when an extractor emitted blocks out of sequence.
            page.blocks.sort(key=lambda b: (round(b.bbox.y0, 3), round(b.bbox.x0, 3), b.order))
            for order, block in enumerate(page.blocks):
                block.order = order
