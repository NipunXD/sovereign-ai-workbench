"""Tools that produce documents.

Every one of these is gated. They are the only tools whose output leaves the
system — a Word report gets emailed, a spreadsheet gets pasted into a
turnaround plan — and once it does, nothing about how it was made travels with
it except the provenance page written inside.

The approval requirement is declared on the spec and enforced by the dispatcher,
never asked of the model.

A note on the input schemas below. They are deliberately *flat* — arrays of
strings and one level of nesting — rather than the richer models the builders
accept. Constrained decoding against a schema full of $refs and nested objects
is punishingly slow on a local model: the first version of this tool exposed the
builder's own XlsxSpec and argument generation timed out after five minutes
without producing anything. The tool's interface is shaped for the model that
has to fill it in; the expressive model stays an implementation detail on the
other side of a mapping function.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from workbench.artifacts.base import ArtifactBytes
from workbench.artifacts.docx_builder import Block, DocxBuilder, DocxSpec
from workbench.artifacts.pptx_builder import PptxBuilder, PptxSpec, Slide
from workbench.artifacts.provenance import Provenance
from workbench.artifacts.store import ArtifactStore
from workbench.artifacts.xlsx_builder import (
    ChartSpec,
    Column,
    Sheet,
    XlsxBuilder,
    XlsxSpec,
)
from workbench.core.logging import get_logger
from workbench.tools.base import BaseTool, ToolContext, ToolResult, ToolSpec

log = get_logger(__name__)


class ArtifactOutput(BaseModel):
    artifact_id: str
    filename: str
    kind: str
    mime: str
    sha256: str
    size_bytes: int
    download_url: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReportSection(BaseModel):
    """One section of a report. One level of nesting, no more."""

    heading: str = Field(description="Section heading")
    body: str = Field(default="", description="Prose for this section")
    bullets: list[str] = Field(default_factory=list, description="Bullet points")
    table_headers: list[str] = Field(default_factory=list, description="Table column headers")
    table_rows: list[list[str]] = Field(
        default_factory=list, description="Table rows, aligned to table_headers"
    )


class DocxInput(BaseModel):
    title: str = Field(description="Report title")
    subtitle: str = Field(default="", description="Optional subtitle")
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    sections: list[ReportSection] = Field(default_factory=list)


class XlsxInput(BaseModel):
    title: str = Field(description="Workbook title")
    sheet_name: str = Field(default="Data", description="Name of the sheet")
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    headers: list[str] = Field(description="Column headers, left to right")
    rows: list[list[str]] = Field(default_factory=list, description="Data rows, aligned to headers")
    numeric_headers: list[str] = Field(
        default_factory=list,
        description="Headers whose values are numbers, so Excel treats them as such",
    )
    formulas: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Extra computed columns as header -> Excel formula, using {row} for the "
            'current row. Example: {"Rate (mm/yr)": "=(B{row}-C{row})/6"}. Prefer a '
            "formula over a precomputed number so the sheet recalculates when a "
            "reading is edited."
        ),
    )
    chart_of: list[str] = Field(
        default_factory=list,
        description="Headers to plot as a line chart, if a chart is wanted",
    )
    notes: list[str] = Field(default_factory=list)


class BriefingSlide(BaseModel):
    """One slide. Flat, for the same reason the other inputs are."""

    heading: str = Field(description="Slide title")
    bullets: list[str] = Field(
        default_factory=list,
        description="Bullet points. Keep each to one line; long ones do not fit on a slide.",
    )
    table_headers: list[str] = Field(default_factory=list, description="Table column headers")
    table_rows: list[list[str]] = Field(
        default_factory=list, description="Table rows, aligned to table_headers"
    )
    footnote: str = Field(
        default="",
        description=(
            "Source reference shown at the foot of the slide, e.g. 'INSP-2029-V1201 p.2'. "
            "A slide is forwarded on its own more often than any other format, so say "
            "where the figures came from on the slide itself."
        ),
    )


class PptxInput(BaseModel):
    title: str = Field(description="Deck title")
    subtitle: str = Field(default="", description="Optional subtitle")
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    slides: list[BriefingSlide] = Field(default_factory=list)


class _ArtifactTool(BaseTool):
    """Shared plumbing: build, stamp provenance, store."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def _finish(
        self, artifact: ArtifactBytes, provenance: Provenance, ctx: ToolContext
    ) -> ToolResult:
        # The digest is stamped after the bytes exist, so it identifies the
        # exact file a recipient ends up holding.
        provenance.sha256 = artifact.sha256
        stored = self.store.put(artifact)

        payload = ArtifactOutput(
            artifact_id=stored.sha256,
            filename=stored.filename,
            kind=artifact.kind.value,
            mime=stored.mime,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            download_url=f"/api/v1/artifacts/{stored.sha256}/download",
            provenance=provenance.as_dict(),
        )
        return ToolResult(
            ok=True,
            data=payload,
            artifacts=[payload.model_dump()],
            metrics={
                "kind": artifact.kind.value,
                "size_bytes": stored.size_bytes,
                "deduplicated": not stored.newly_written,
                "uncertain_sources": provenance.has_uncertain_sources,
            },
        )

    @staticmethod
    def _provenance(ctx: ToolContext) -> Provenance:
        """Build the provenance record from the run's own context.

        Taken from the run rather than from the model's arguments: a model asked
        to state which sources it used would be free to invent them, and the
        whole point of the block is that it cannot.
        """
        run = ctx.run_context or {}
        return Provenance(
            run_id=ctx.run_id,
            generated_by=ctx.principal.username,
            models=dict(run.get("models") or {}),
            tools_used=list(run.get("tools") or []),
            citations=list(run.get("citations") or []),
            # Known before generation because the gate decides first, so the
            # approver's name is written into the document itself rather than
            # only into a database row the recipient never sees.
            approved_by=run.get("approved_by"),
            approved_at=run.get("approved_at"),
        )


class DocxTool(_ArtifactTool):
    spec = ToolSpec(
        name="artifact.docx",
        description=(
            "Generate a Word report. Provide a title and an ordered list of sections, "
            "each with a heading and any of: body prose, bullets, or a table given as "
            "headers plus rows. A provenance page listing every source is appended "
            "automatically."
        ),
        input_model=DocxInput,
        output_model=ArtifactOutput,
        required_permissions=frozenset({"artifact:generate"}),
        side_effect="write",
        requires_approval=True,
        timeout_s=60,
    )

    def __init__(self, store: ArtifactStore, images: dict[str, bytes] | None = None) -> None:
        super().__init__(store)
        self.images = images or {}

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, DocxInput)
        provenance = self._provenance(ctx)
        artifact = DocxBuilder(images=self.images).build(_to_docx_spec(args), provenance)
        return self._finish(artifact, provenance, ctx)


class XlsxTool(_ArtifactTool):
    spec = ToolSpec(
        name="artifact.xlsx",
        description=(
            "Generate an Excel workbook from headers and rows. Name any numeric columns "
            "in numeric_headers so Excel treats them as numbers. Add computed columns "
            "through formulas, as header -> Excel expression using {row}, e.g. "
            '{"Rate (mm/yr)": "=(B{row}-C{row})/6"} — use a formula rather than a '
            "precomputed value so the sheet recalculates when a reading is edited."
        ),
        input_model=XlsxInput,
        output_model=ArtifactOutput,
        required_permissions=frozenset({"artifact:generate"}),
        side_effect="write",
        requires_approval=True,
        timeout_s=60,
    )

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, XlsxInput)
        provenance = self._provenance(ctx)
        artifact = XlsxBuilder().build(_to_xlsx_spec(args), provenance)
        return self._finish(artifact, provenance, ctx)


class PptxTool(_ArtifactTool):
    spec = ToolSpec(
        name="artifact.pptx",
        description=(
            "Generate a PowerPoint briefing deck. Provide a title and an ordered list of "
            "slides, each with a heading and either bullets or a table given as headers "
            "plus rows. Keep bullets short — this is a deck, not a report; use "
            "artifact.docx when the content needs prose. Every slide carries the "
            "classification marking, and provenance and source slides are appended "
            "automatically."
        ),
        input_model=PptxInput,
        output_model=ArtifactOutput,
        required_permissions=frozenset({"artifact:generate"}),
        side_effect="write",
        requires_approval=True,
        timeout_s=60,
    )

    def __init__(self, store: ArtifactStore, images: dict[str, bytes] | None = None) -> None:
        super().__init__(store)
        self.images = images or {}

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, PptxInput)
        provenance = self._provenance(ctx)
        artifact = PptxBuilder(images=self.images).build(_to_pptx_spec(args), provenance)
        return self._finish(artifact, provenance, ctx)


# --- flat tool input -> expressive builder spec ------------------------------

# --- normalising model-authored text ------------------------------------------

#: Headings the builders own. A model-authored section claiming one of these is
#: renamed, never dropped — its content may be useful, but it must not sit
#: above the real provenance page wearing the same title.
RESERVED_HEADINGS = {"provenance", "sources", "source", "references", "citations"}


def _lines(text: str) -> list[str]:
    """Split model text into lines, including newlines it only *described*.

    Models filling a JSON string field routinely emit a literal backslash-n
    rather than a real line break, and the two are indistinguishable to a
    reader of the finished document — except that one of them renders as the
    characters `\n` in the middle of a sentence. A generated report that says
    "Key findings include:\n- CML-04 showed the highest loss" is one an
    engineer has to mentally repair, so both forms become real breaks here.
    """
    normalised = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")
    return [
        cleaned for line in normalised.splitlines() if (cleaned := _strip_json_debris(line.strip()))
    ]


#: Structural JSON left at the end of a value when the model's argument object
#: was truncated mid-generation. Observed as a report line ending
#: `... 7. Retirement Criteria."}],`.
#:
#: The *closing quote* is what makes this safe to match. A first attempt keyed
#: on the brackets alone and mangled legitimate prose — "(see figure 2)" lost
#: its bracket and "[12.50, 9.20]" lost its own — which is a worse fault than
#: the one being fixed, because it corrupts correct text silently. Requiring a
#: quote before the bracket run matches the truncation artefact and nothing an
#: engineer would actually write.
_JSON_DEBRIS = re.compile(r"""["'`]\s*[}\])][\s"'`,;:}\])]*$""")


def _strip_json_debris(line: str) -> str:
    """Remove a trailing fragment of the enclosing JSON from model text."""
    if not line:
        return line
    trimmed = _JSON_DEBRIS.sub("", line)
    # If the pattern would eat the whole line, it was not debris.
    return trimmed if trimmed.strip() else line


def _body_blocks(text: str) -> list[Block]:
    """One block per line, promoting lines the model wrote as list items."""
    blocks: list[Block] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            blocks.append(Block(type="bullets", items=list(pending)))
            pending.clear()

    for line in _lines(text):
        stripped = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", "", line)
        if stripped != line:
            pending.append(stripped)
        else:
            flush()
            blocks.append(Block(type="paragraph", text=line))
    flush()
    return blocks


def _safe_heading(heading: str) -> str:
    """Keep a model-authored section out of the builder's reserved namespace."""
    if heading.strip().lower().rstrip(":") in RESERVED_HEADINGS:
        return f"{heading.strip().rstrip(':')} (as stated in the answer)"
    return heading


def _to_docx_spec(args: DocxInput) -> DocxSpec:
    blocks: list[Block] = []
    for section in args.sections:
        blocks.append(Block(type="heading", text=_safe_heading(section.heading), level=1))
        if section.body:
            blocks.extend(_body_blocks(section.body))
        if section.bullets:
            blocks.append(
                Block(type="bullets", items=[b for s in section.bullets for b in _lines(s)])
            )
        if section.table_headers and section.table_rows:
            blocks.append(
                Block(
                    type="table",
                    rows=[
                        section.table_headers,
                        *[[str(c) for c in r] for r in section.table_rows],
                    ],
                )
            )
    return DocxSpec(
        title=args.title,
        subtitle=args.subtitle,
        classification=args.classification,
        blocks=blocks,
    )


def _to_xlsx_spec(args: XlsxInput) -> XlsxSpec:
    numeric = {h.strip().lower() for h in args.numeric_headers}
    columns = [
        Column(
            header=header,
            kind="number" if header.strip().lower() in numeric else "text",
            number_format="0.00" if header.strip().lower() in numeric else "",
        )
        for header in args.headers
    ]
    rows: list[list[Any]] = [list(row) for row in args.rows]

    # Formula columns are appended, and every row gets the expression. The
    # builder substitutes {row} for the actual sheet row.
    for header, expression in args.formulas.items():
        columns.append(Column(header=header, kind="formula", number_format="0.000"))
        for row in rows:
            row.append(expression)

    chart = None
    if args.chart_of:
        all_headers = [c.header for c in columns]
        indices = [all_headers.index(h) + 1 for h in args.chart_of if h in all_headers]
        if indices:
            chart = ChartSpec(
                type="line", title=args.title, categories_column=1, value_columns=indices
            )

    return XlsxSpec(
        title=args.title,
        classification=args.classification,
        sheets=[
            Sheet(
                name=args.sheet_name,
                columns=columns,
                rows=rows,
                chart=chart,
                notes=args.notes,
            )
        ],
    )


def _to_pptx_spec(args: PptxInput) -> PptxSpec:
    slides: list[Slide] = []
    for entry in args.slides:
        # A table and bullets on one slide is unreadable, so a slide carrying
        # both becomes two. The alternative — dropping one — loses content the
        # model meant to show.
        if entry.bullets:
            slides.append(
                Slide(
                    type="bullets",
                    title=_safe_heading(entry.heading),
                    # Same escaped-newline problem as the report: a bullet
                    # holding two lines renders as one unreadable run.
                    bullets=[b for raw in entry.bullets for b in _lines(raw)],
                    notes=entry.footnote,
                )
            )
        if entry.table_headers or entry.table_rows:
            rows = [list(entry.table_headers)] if entry.table_headers else []
            rows += [list(row) for row in entry.table_rows]
            slides.append(
                Slide(
                    type="table",
                    title=(
                        _safe_heading(entry.heading)
                        if not entry.bullets
                        else f"{_safe_heading(entry.heading)} — data"
                    ),
                    rows=rows,
                    notes=entry.footnote,
                )
            )
        if not entry.bullets and not entry.table_headers and not entry.table_rows:
            slides.append(
                Slide(type="section", title=_safe_heading(entry.heading), notes=entry.footnote)
            )

    return PptxSpec(
        title=args.title,
        subtitle=args.subtitle,
        classification=args.classification,
        slides=slides,
    )
