"""Standalone image extraction.

A photograph of a nameplate, a phone picture of a gauge, an exported P&ID. There
is no text layer at all, so the page is created empty and the OCR and vision
stages fill it in — which is exactly the path a scanned PDF page takes, so the
two share the same downstream handling.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from workbench.core.ir import DocumentIR, ExtractionReport, Page


class ImageExtractor:
    name = "image"

    def __init__(self, viewer_dpi: int = 150) -> None:
        self.viewer_dpi = viewer_dpi

    def extract(
        self,
        path: Path,
        doc_id: str,
        *,
        image_dir: Path | None = None,
        filename: str | None = None,
        **_: Any,
    ) -> DocumentIR:
        from PIL import Image

        started = time.perf_counter()
        with Image.open(path) as image:
            width, height = image.size
            fmt = (image.format or "").lower()

            image_ref = None
            if image_dir is not None:
                image_dir.mkdir(parents=True, exist_ok=True)
                target = image_dir / "page_0001.png"
                # Normalised to PNG so the viewer has one format to handle, and
                # so a CMYK TIFF from a scanner renders in a browser at all.
                image.convert("RGB").save(target, "PNG")
                image_ref = str(target)

        return DocumentIR(
            doc_id=doc_id,
            title=Path(filename or path.name).stem,
            mime=f"image/{fmt or 'png'}",
            page_count=1,
            # Deliberately blockless: OCR and the vision stage populate it.
            pages=[Page(page_no=1, width=width, height=height, image_ref=image_ref, blocks=[])],
            metadata={"source_format": fmt, "pixels": f"{width}x{height}"},
            extraction_report=ExtractionReport(
                extractor=self.name,
                pages_processed=1,
                blocks_extracted=0,
                stage_timings_ms={"total": (time.perf_counter() - started) * 1000},
                warnings=["image has no text layer; recognition is required"],
            ),
        )
