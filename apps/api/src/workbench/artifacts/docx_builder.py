"""Word report generation.

Produces the document an inspection engineer would actually circulate: a
classification banner, numbered sections, tables that keep their headers, and a
provenance page at the end recording where every figure came from.

Determinism matters here. Under WORKBENCH_DETERMINISTIC the document properties
carry a fixed timestamp, so the same inputs produce byte-identical output and a
golden-file test is possible. Word writes a creation time by default, which
would otherwise change on every run.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from workbench.artifacts.base import ArtifactBytes, ArtifactKind
from workbench.artifacts.provenance import AI_NOTICE, Provenance
from workbench.core.clock import now
from workbench.core.hashing import digest_bytes


class Block(BaseModel):
    """One element of a report."""

    type: Literal["heading", "paragraph", "table", "bullets", "image", "pagebreak"]
    text: str = ""
    level: int = 1
    #: Table rows, first row treated as the header.
    rows: list[list[str]] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    #: Reference to a file produced by the sandbox, resolved by the caller.
    image_ref: str = ""
    caption: str = ""


class DocxSpec(BaseModel):
    title: str
    subtitle: str = ""
    classification: str = "internal"
    blocks: list[Block] = Field(default_factory=list)


class DocxBuilder:
    kind = ArtifactKind.DOCX

    def __init__(self, images: dict[str, bytes] | None = None) -> None:
        #: image_ref -> bytes, supplied by the caller from sandbox output.
        self.images = images or {}

    def build(self, spec: DocxSpec, provenance: Provenance) -> ArtifactBytes:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt, RGBColor

        document = Document()

        # Classification in the header of every page. A controlled document that
        # only says "confidential" on the cover is one photocopied page away
        # from being unmarked.
        section = document.sections[0]
        header = section.header.paragraphs[0]
        header.text = f"MRPL — {spec.classification.upper()}"
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in header.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

        document.add_heading(spec.title, level=0)
        if spec.subtitle:
            subtitle = document.add_paragraph(spec.subtitle)
            subtitle.runs[0].font.size = Pt(11)
            subtitle.runs[0].font.color.rgb = RGBColor(0x60, 0x60, 0x60)

        for block in spec.blocks:
            self._render(document, block)

        self._provenance_page(document, provenance)

        buffer = io.BytesIO()
        # A fixed creation time under deterministic mode; python-docx would
        # otherwise stamp the current time and defeat golden-file comparison.
        document.core_properties.created = now()
        document.core_properties.modified = now()
        document.core_properties.author = "MRPL Sovereign AI Workbench"
        document.core_properties.title = spec.title
        document.save(buffer)

        data = buffer.getvalue()
        return ArtifactBytes(
            data=data,
            kind=self.kind,
            filename=self._filename(spec.title),
            sha256=digest_bytes(data),
            meta={"blocks": len(spec.blocks), "classification": spec.classification},
        )

    def _render(self, document: Any, block: Block) -> None:
        from docx.shared import Inches, Pt

        if block.type == "heading":
            document.add_heading(block.text, level=min(max(block.level, 1), 4))

        elif block.type == "paragraph":
            document.add_paragraph(block.text)

        elif block.type == "bullets":
            for item in block.items:
                document.add_paragraph(item, style="List Bullet")

        elif block.type == "table" and block.rows:
            columns = max(len(row) for row in block.rows)
            table = document.add_table(rows=0, cols=columns)
            table.style = "Table Grid"
            for index, row in enumerate(block.rows):
                cells = table.add_row().cells
                for position in range(columns):
                    value = row[position] if position < len(row) else ""
                    cells[position].text = str(value)
                    if index == 0:
                        # The header row must survive a page break, or a table
                        # of readings continues onto page two unlabelled.
                        for paragraph in cells[position].paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
            table.rows[0]._tr.get_or_add_trPr().append(
                _repeat_header_row()
            )
            if block.caption:
                caption = document.add_paragraph(block.caption)
                caption.runs[0].font.size = Pt(8)

        elif block.type == "image" and block.image_ref in self.images:
            document.add_picture(io.BytesIO(self.images[block.image_ref]), width=Inches(6.0))
            if block.caption:
                caption = document.add_paragraph(block.caption)
                caption.runs[0].font.size = Pt(8)

        elif block.type == "pagebreak":
            document.add_page_break()

    def _provenance_page(self, document: Any, provenance: Provenance) -> None:
        from docx.shared import Pt, RGBColor

        document.add_page_break()
        document.add_heading("Provenance", level=1)

        notice = document.add_paragraph(AI_NOTICE)
        notice.runs[0].font.size = Pt(9)
        notice.runs[0].italic = True

        if provenance.has_uncertain_sources:
            warning = document.add_paragraph(
                "Some figures in this document were read from scanned pages by "
                "optical character recognition and may be inaccurate. Sources "
                "affected are marked below."
            )
            warning.runs[0].font.size = Pt(9)
            warning.runs[0].bold = True
            warning.runs[0].font.color.rgb = RGBColor(0xB0, 0x50, 0x00)

        table = document.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        for label, value in provenance.lines():
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = value
            for paragraph in cells[0].paragraphs:
                for run in paragraph.runs:
                    run.bold = True
            for cell in cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(8)

        document.add_heading("Sources", level=2)
        source_lines = provenance.source_lines()
        if source_lines:
            for line in source_lines:
                paragraph = document.add_paragraph(line, style="List Number")
                paragraph.runs[0].font.size = Pt(9)
        else:
            paragraph = document.add_paragraph(
                "No documents were cited. This content was not grounded in the "
                "indexed corpus."
            )
            paragraph.runs[0].font.size = Pt(9)
            paragraph.runs[0].bold = True

    @staticmethod
    def _filename(title: str) -> str:
        import re

        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60] or "report"
        return f"{slug}.docx"


def _repeat_header_row() -> Any:
    """Mark a table's first row to repeat on each page.

    python-docx has no API for this; it is a single OOXML element.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    return header
