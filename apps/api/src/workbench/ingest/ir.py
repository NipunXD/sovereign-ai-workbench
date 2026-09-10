"""The intermediate document representation.

Every extractor — native PDF, scanned PDF, image, Word, PowerPoint, Excel, CSV,
email — produces a ``DocumentIR``. Everything downstream consumes only this:
chunking, citation payloads, the viewer's highlight overlay, and the OCR eval
harness. Getting this contract right matters more than any individual extractor,
because changing it later means changing all of them.

Two decisions carry most of the weight:

* **Bounding boxes are normalised to 0..1**, origin top-left. A citation stored
  in page pixels breaks the moment the viewer renders at a different zoom or the
  page is re-rendered at another DPI. Normalised, the overlay is a
  multiplication and survives both.
* **Every block records its ``source``** — native text layer, OCR, or a vision
  model. An answer resting on OCR at 61% confidence is not the same as one
  resting on an embedded text layer, and the user is entitled to see which.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class BlockType(StrEnum):
    """What a laid-out region of a page is."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    FIGURE = "figure"
    CAPTION = "caption"
    FORMULA = "formula"
    #: Handwritten margin notes — common on scanned inspection reports, and
    #: usually the most operationally interesting text on the page.
    HANDWRITING = "handwriting"
    #: Tags and callouts extracted from an engineering drawing.
    DRAWING_ANNOTATION = "drawing_annotation"
    FOOTER = "footer"
    HEADER = "header"


class BlockSource(StrEnum):
    """How the text was obtained. Drives confidence and the citation badge."""

    NATIVE = "native"  # embedded PDF text layer — exact
    OFFICE = "office"  # docx/pptx/xlsx object model — exact
    OCR = "ocr"  # rasterised and recognised — approximate
    VLM = "vlm"  # read by a vision model — approximate, unordered


class BBox(BaseModel):
    """A normalised rectangle: 0..1 of page width/height, origin top-left."""

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 1.0
    y1: float = 1.0

    @field_validator("x0", "y0", "x1", "y1")
    @classmethod
    def _clamp(cls, v: float) -> float:
        # Extractors occasionally emit coordinates a hair outside the page.
        # Clamping here keeps a malformed box from rendering an overlay off
        # screen, which looks like a broken citation to the user.
        return max(0.0, min(1.0, float(v)))

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.x1 < self.x0:
            self.x0, self.x1 = self.x1, self.x0
        if self.y1 < self.y0:
            self.y0, self.y1 = self.y1, self.y0
        return self

    @classmethod
    def from_pixels(
        cls, rect: tuple[float, float, float, float], width: float, height: float
    ) -> BBox:
        """Normalise a pixel rectangle against its page dimensions."""
        if width <= 0 or height <= 0:
            return cls()
        x0, y0, x1, y1 = rect
        return cls(x0=x0 / width, y0=y0 / height, x1=x1 / width, y1=y1 / height)

    def union(self, other: BBox) -> BBox:
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def as_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


class Block(BaseModel):
    """One laid-out region of a page."""

    block_id: str
    page_no: int = 1
    type: BlockType = BlockType.PARAGRAPH
    text: str = ""
    bbox: BBox = Field(default_factory=BBox)
    #: 1.0 for exact sources; the recogniser's confidence otherwise.
    confidence: float = 1.0
    source: BlockSource = BlockSource.NATIVE
    #: Reading order within the page.
    order: int = 0
    #: Type-specific extras: table_html, heading level, detected equipment tags.
    attrs: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_exact(self) -> bool:
        """Whether the text is transcribed rather than recognised."""
        return self.source in (BlockSource.NATIVE, BlockSource.OFFICE)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class Page(BaseModel):
    """One page, with its rendered image and its blocks in reading order."""

    page_no: int = 1
    width: int = 0
    height: int = 0
    #: Path to the rendered PNG the viewer displays.
    image_ref: str | None = None
    blocks: list[Block] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in sorted(self.blocks, key=lambda b: b.order) if b.text)

    @property
    def mean_confidence(self) -> float:
        """Word-weighted mean confidence.

        Weighted by words rather than by block so that a single high-confidence
        page number cannot mask a paragraph the recogniser struggled with.
        """
        weighted = [(b.confidence, b.word_count) for b in self.blocks if b.word_count]
        if not weighted:
            return 1.0
        total_words = sum(words for _, words in weighted)
        return sum(conf * words for conf, words in weighted) / total_words

    @property
    def word_count(self) -> int:
        return sum(b.word_count for b in self.blocks)

    @property
    def is_blank(self) -> bool:
        return self.word_count == 0


class DatasetRef(BaseModel):
    """A tabular sheet lifted out for querying rather than embedding.

    A maintenance log with 4,000 rows is useless as prose in a context window;
    it belongs in DuckDB where the agent can aggregate over it.
    """

    dataset_id: str
    sheet_name: str = ""
    row_count: int = 0
    columns: list[dict[str, Any]] = Field(default_factory=list)
    path: str = ""


class ExtractionReport(BaseModel):
    """What happened during extraction. Surfaced in the document detail view."""

    extractor: str = ""
    pages_processed: int = 0
    pages_ocr: int = 0
    pages_vlm_escalated: int = 0
    blocks_extracted: int = 0
    mean_confidence: float = 1.0
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    #: Set when the pipeline finished but the result is not trustworthy —
    #: surfaced prominently rather than allowing a silently bad index.
    degraded: bool = False


class DocumentIR(BaseModel):
    """The complete extracted representation of one document."""

    doc_id: str
    title: str = ""
    mime: str = ""
    page_count: int = 0
    pages: list[Page] = Field(default_factory=list)
    datasets: list[DatasetRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extraction_report: ExtractionReport = Field(default_factory=ExtractionReport)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def blocks(self) -> list[Block]:
        return [block for page in self.pages for block in page.blocks]

    @property
    def mean_confidence(self) -> float:
        weighted = [(p.mean_confidence, p.word_count) for p in self.pages if p.word_count]
        if not weighted:
            return 1.0
        total = sum(words for _, words in weighted)
        return sum(conf * words for conf, words in weighted) / total

    def page(self, page_no: int) -> Page | None:
        return next((p for p in self.pages if p.page_no == page_no), None)

    def blocks_of_type(self, *types: BlockType) -> list[Block]:
        wanted = set(types)
        return [b for b in self.blocks if b.type in wanted]

    def low_confidence_pages(self, threshold: float = 0.72) -> list[Page]:
        """Pages the quality gate should consider escalating to a vision model."""
        return [p for p in self.pages if not p.is_blank and p.mean_confidence < threshold]
