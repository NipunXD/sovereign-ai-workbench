"""Which pages get handed to the vision model, and which must not be.

Escalation is the most expensive thing the pipeline can do — a model load plus
a multi-minute generation per page. Spending it on a page that already has
perfect text is not merely wasteful: it is the difference between an upload
that finishes in seconds and one that appears to hang.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.core.ir import BBox, Block, BlockSource, BlockType, DocumentIR, Page
from workbench.ingest.pipeline import IngestionPipeline, PipelineConfig


class _RecordingVision:
    def __init__(self) -> None:
        self.pages: list[int] = []

    async def transcribe_page(self, raw: bytes, page_no: int):
        self.pages.append(page_no)
        return [], 0.0


_BOX = BBox(x0=0.0, y0=0.0, x1=1.0, y1=0.1)


def _page(page_no: int, text: str) -> Page:
    page = Page(page_no=page_no, width=1240, height=1754)
    if text:
        page.blocks.append(
            Block(
                block_id=f"b{page_no}",
                page_no=page_no,
                type=BlockType.PARAGRAPH,
                text=text,
                bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=0.1),
                confidence=1.0,
                source=BlockSource.NATIVE,
                order=0,
            )
        )
    return page


def _pipeline(vision: _RecordingVision) -> IngestionPipeline:
    return IngestionPipeline(
        blob_store=None,  # type: ignore[arg-type]
        page_image_dir=Path("/tmp"),
        dataset_dir=Path("/tmp"),
        chunker=None,
        vision_reader=vision,
        config=PipelineConfig(vision_enabled=True),
    )


@pytest.mark.anyio
async def test_a_page_with_its_own_text_layer_is_never_sent_to_vision() -> None:
    """The regression: no OcrResult means "not OCR'd", not "OCR found nothing".

    Treating the two as the same escalated every native PDF page. One such
    upload spent five minutes in a vision call before timing out, on text the
    extractor had already read exactly.
    """
    vision = _RecordingVision()
    ir = DocumentIR(doc_id="d1", mime="application/pdf")
    ir.pages.append(_page(1, "Design pressure 12.5 barg. Material SA-516 Gr.70."))

    escalated, failed = await _pipeline(vision)._run_vision(
        ir, {}, Path("/tmp/x"), "application/pdf"
    )

    assert vision.pages == []
    assert (escalated, failed) == (0, False)


@pytest.mark.anyio
async def test_a_badly_ocred_page_still_escalates() -> None:
    """The guard must not swallow the case escalation exists for."""
    from workbench.ingest.ocr.engine import OcrLine, OcrResult

    vision = _RecordingVision()
    ir = DocumentIR(doc_id="d2", mime="application/pdf")
    ir.pages.append(_page(1, "MRPL WOAK PERMET WF-2030"))
    scanned = OcrResult(
        lines=[OcrLine(text="MRPL WOAK PERMET WF-2030", confidence=0.41, bbox=_BOX)],
        engine="rapidocr",
    )

    pipeline = _pipeline(vision)
    pipeline._page_image = lambda *a, **k: b"png-bytes"  # type: ignore[method-assign]
    await pipeline._run_vision(ir, {1: scanned}, Path("/tmp/x"), "application/pdf")

    assert vision.pages == [1]
