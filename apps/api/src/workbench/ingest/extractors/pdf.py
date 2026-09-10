"""PDF extraction.

A PDF is either *native* (carries a real text layer) or *scanned* (a picture of
a page). The difference determines everything downstream — a native page yields
exact text with exact coordinates, while a scanned page needs OCR and produces
approximate text with a confidence score.

Deciding which is which is not a binary check on "does it have any text". Real
refinery documents are routinely hybrids: a native cover sheet in front of
scanned inspection forms, or a scanned page with a native stamp overlaid. So the
decision is made per page, on text density.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pymupdf

from workbench.core.logging import get_logger
from workbench.ingest.ir import (
    BBox,
    Block,
    BlockSource,
    BlockType,
    DocumentIR,
    ExtractionReport,
    Page,
)

log = get_logger(__name__)

#: Below this many words, a page's text layer is not a real one. Scanners often
#: embed a handful of junk characters, which would otherwise read as "native".
MIN_WORDS_FOR_NATIVE = 12

#: Fraction of a page's area that must be covered by images before it is treated
#: as a scan even when some text is present.
IMAGE_COVERAGE_SCANNED = 0.55


class PdfExtractor:
    """Extracts a DocumentIR from a PDF, page by page."""

    name = "pdf"

    def __init__(self, *, viewer_dpi: int = 150, ocr_dpi: int = 300) -> None:
        self.viewer_dpi = viewer_dpi
        self.ocr_dpi = ocr_dpi

    def extract(self, path: Path, doc_id: str, *, image_dir: Path | None = None) -> DocumentIR:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        warnings: list[str] = []

        with pymupdf.open(path) as pdf:
            metadata = dict(pdf.metadata or {})
            pages: list[Page] = []
            native_pages = scanned_pages = 0

            for index, pdf_page in enumerate(pdf, start=1):
                page_started = time.perf_counter()
                page, is_native = self._extract_page(pdf_page, index, image_dir)
                pages.append(page)
                native_pages += is_native
                scanned_pages += not is_native
                timings[f"page_{index}"] = (time.perf_counter() - page_started) * 1000

        if scanned_pages:
            warnings.append(
                f"{scanned_pages} of {len(pages)} pages have no usable text layer "
                f"and require OCR"
            )

        report = ExtractionReport(
            extractor=self.name,
            pages_processed=len(pages),
            pages_ocr=0,  # the OCR stage fills this in
            blocks_extracted=sum(len(p.blocks) for p in pages),
            mean_confidence=1.0 if not scanned_pages else 0.0,
            stage_timings_ms={"total": (time.perf_counter() - started) * 1000, **timings},
            warnings=warnings,
        )

        return DocumentIR(
            doc_id=doc_id,
            title=str(metadata.get("title") or path.stem),
            mime="application/pdf",
            page_count=len(pages),
            pages=pages,
            metadata={
                "author": metadata.get("author"),
                "created": metadata.get("creationDate"),
                "producer": metadata.get("producer"),
                "native_pages": native_pages,
                "scanned_pages": scanned_pages,
            },
            extraction_report=report,
        )

    # ------------------------------------------------------------------ page
    def _extract_page(
        self, pdf_page: pymupdf.Page, page_no: int, image_dir: Path | None
    ) -> tuple[Page, bool]:
        rect = pdf_page.rect
        width, height = int(rect.width), int(rect.height)

        image_ref = None
        if image_dir is not None:
            image_ref = self._render(pdf_page, page_no, image_dir)

        blocks, is_native = self._text_blocks(pdf_page, page_no, width, height)

        return (
            Page(
                page_no=page_no,
                width=width,
                height=height,
                image_ref=image_ref,
                blocks=blocks,
            ),
            is_native,
        )

    def _text_blocks(
        self, pdf_page: pymupdf.Page, page_no: int, width: int, height: int
    ) -> tuple[list[Block], bool]:
        """Pull the text layer, if there is a usable one."""
        raw = pdf_page.get_text("dict")
        text_blocks = [b for b in raw.get("blocks", []) if b.get("type") == 0]

        word_count = sum(
            len(span.get("text", "").split())
            for block in text_blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        )

        if word_count < MIN_WORDS_FOR_NATIVE or self._image_coverage(raw, width, height) > IMAGE_COVERAGE_SCANNED:
            # Leave the page empty. The OCR stage recognises pages with no
            # blocks and fills them in; emitting a handful of junk characters
            # here would suppress that and leave the page unsearchable.
            return [], False

        blocks: list[Block] = []
        body_size = self._body_font_size(text_blocks)

        for order, block in enumerate(text_blocks):
            text_parts: list[str] = []
            max_size = 0.0
            bold = False
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text_parts.append(span.get("text", ""))
                    max_size = max(max_size, float(span.get("size", 0)))
                    bold = bold or bool(int(span.get("flags", 0)) & 2**4)
            text = "".join(text_parts).strip()
            if not text:
                continue

            blocks.append(
                Block(
                    block_id=f"p{page_no}b{order}",
                    page_no=page_no,
                    type=self._classify(text, max_size, body_size, bold, block, height),
                    text=text,
                    bbox=BBox.from_pixels(tuple(block.get("bbox", (0, 0, 0, 0))), width, height),
                    confidence=1.0,
                    source=BlockSource.NATIVE,
                    order=order,
                    attrs=(
                        {"level": self._heading_level(max_size, body_size)}
                        if self._is_heading(text, max_size, body_size, bold)
                        else {}
                    ),
                )
            )
        return blocks, True

    @staticmethod
    def _image_coverage(raw: dict[str, Any], width: int, height: int) -> float:
        """Fraction of the page covered by image blocks."""
        page_area = float(width * height) or 1.0
        covered = 0.0
        for block in raw.get("blocks", []):
            if block.get("type") != 1:
                continue
            x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
            covered += abs((x1 - x0) * (y1 - y0))
        return min(1.0, covered / page_area)

    @staticmethod
    def _body_font_size(text_blocks: list[dict[str, Any]]) -> float:
        """The most common font size, taken as body text.

        Headings are then identified relative to this rather than against a
        fixed point size, which varies wildly between document templates.
        """
        from collections import Counter

        sizes: Counter[float] = Counter()
        for block in text_blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(float(span.get("size", 0)), 1)
                    if size:
                        sizes[size] += len(span.get("text", ""))
        return sizes.most_common(1)[0][0] if sizes else 10.0

    @staticmethod
    def _is_heading(text: str, size: float, body_size: float, bold: bool) -> bool:
        if len(text) > 160 or text.endswith((".", ";")):
            return False
        # Decorative glyphs — arrows in a workflow diagram, rules, bullets — are
        # often set larger than body text. Treating them as headings poisons the
        # section path carried into every chunk beneath them.
        if not any(ch.isalnum() for ch in text):
            return False
        if size > body_size * 1.15:
            return True
        # Numbered clause headings ("5.2 Depressurisation") are the dominant
        # heading style in SOPs and often use the body font at bold weight.
        import re

        return bool(bold and re.match(r"^\d+(\.\d+)*\s+\S", text))

    @staticmethod
    def _heading_level(size: float, body_size: float) -> int:
        ratio = size / body_size if body_size else 1.0
        if ratio >= 1.6:
            return 1
        if ratio >= 1.3:
            return 2
        return 3

    def _classify(
        self,
        text: str,
        size: float,
        body_size: float,
        bold: bool,
        block: dict[str, Any],
        page_height: int,
    ) -> BlockType:
        if self._is_heading(text, size, body_size, bold):
            return BlockType.HEADING
        y0 = float(block.get("bbox", (0, 0, 0, 0))[1])
        y1 = float(block.get("bbox", (0, 0, 0, 0))[3])
        if page_height:
            if y1 < page_height * 0.06:
                return BlockType.HEADER
            if y0 > page_height * 0.94:
                return BlockType.FOOTER
        import re

        if re.match(r"^\s*[-•*•]\s+", text) or re.match(r"^\s*\(?[a-z0-9]\)\s+", text):
            return BlockType.LIST
        return BlockType.PARAGRAPH

    def _render(self, pdf_page: pymupdf.Page, page_no: int, image_dir: Path) -> str:
        """Rasterise a page for the document viewer."""
        image_dir.mkdir(parents=True, exist_ok=True)
        target = image_dir / f"page_{page_no:04d}.png"
        zoom = self.viewer_dpi / 72.0
        pixmap = pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        pixmap.save(target)
        return str(target)

    def render_for_ocr(self, path: Path, page_no: int) -> bytes:
        """Rasterise one page at OCR resolution.

        Higher DPI than the viewer copy: recognition accuracy on small type
        degrades sharply below about 300 dpi, and inspection reports are full of
        small type.
        """
        with pymupdf.open(path) as pdf:
            page = pdf[page_no - 1]
            zoom = self.ocr_dpi / 72.0
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            return bytes(pixmap.tobytes("png"))
