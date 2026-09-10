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

from typing import Any, Literal

from pydantic import BaseModel, Field

from workbench.artifacts.base import ArtifactBytes
from workbench.artifacts.docx_builder import Block, DocxBuilder, DocxSpec
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
    rows: list[list[str]] = Field(
        default_factory=list, description="Data rows, aligned to headers"
    )
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
            'through formulas, as header -> Excel expression using {row}, e.g. '
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


# --- flat tool input -> expressive builder spec ------------------------------


def _to_docx_spec(args: DocxInput) -> DocxSpec:
    blocks: list[Block] = []
    for section in args.sections:
        blocks.append(Block(type="heading", text=section.heading, level=1))
        if section.body:
            blocks.append(Block(type="paragraph", text=section.body))
        if section.bullets:
            blocks.append(Block(type="bullets", items=section.bullets))
        if section.table_headers and section.table_rows:
            blocks.append(
                Block(
                    type="table",
                    rows=[section.table_headers, *[[str(c) for c in r] for r in section.table_rows]],
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
