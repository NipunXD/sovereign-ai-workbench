"""Documents, pages, blocks and chunks.

``Classification`` is imported from ``core`` and re-exported here for the
convenience of call sites already dealing in ORM models.

``chunks`` carries denormalised ``classification`` and ``departments`` columns.
That duplication is deliberate: the RBAC filter has to run *inside* the vector
search and the SQL WHERE clause, and a join to reach the parent document would
either be impossible (Qdrant) or too slow (hot path). Post-filtering results
after retrieval would leak information through scores and result counts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Computed, Float, Index, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlmodel import Field, SQLModel

from workbench.core.classification import Classification
from workbench.core.clock import now
from workbench.core.ids import prefixed_id
from workbench.db.base import UTCDateTime


class DocStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class Document(SQLModel, table=True):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_classification", "classification"),
        Index("ix_documents_type", "doc_type"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("document"), primary_key=True)
    #: Content address. Re-uploading the same file links rather than re-ingests.
    sha256: str = Field(unique=True, index=True, max_length=64)
    title: str = ""
    filename: str = ""
    mime: str = ""
    doc_type: str = "unknown"  # sop | inspection | drawing | tabular | correspondence
    page_count: int = 0
    size_bytes: int = 0

    classification: str = Classification.INTERNAL
    departments: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )
    tags: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )
    owner_user_id: str | None = Field(default=None, foreign_key="users.id")

    status: str = Field(default=DocStatus.PENDING, index=True)
    blob_path: str = ""
    ir_path: str | None = None
    doc_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    extraction_report: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )

    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    indexed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    deleted_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class DocumentPage(SQLModel, table=True):
    __tablename__ = "document_pages"
    __table_args__ = (Index("ix_pages_document_no", "document_id", "page_no", unique=True),)

    id: str = Field(default_factory=lambda: prefixed_id("page"), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    page_no: int = 1
    width: int = 0
    height: int = 0
    image_path: str | None = None
    #: Mean OCR confidence for the page. Surfaced in the viewer so a user can
    #: see when an answer rests on a poorly-scanned source.
    mean_confidence: float = Field(
        default=1.0, sa_column=Column(Float, nullable=False, server_default=sa_text("1"))
    )
    ocr_engine: str | None = None


class DocumentBlock(SQLModel, table=True):
    """A laid-out region of a page: paragraph, table, figure, annotation.

    ``bbox`` is normalised to 0..1 so the frontend highlight overlay is a
    multiplication regardless of zoom or render scale.
    """

    __tablename__ = "document_blocks"
    __table_args__ = (Index("ix_blocks_document_page", "document_id", "page_no"),)

    id: str = Field(default_factory=lambda: prefixed_id("block"), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    page_no: int = 1
    block_id: str = ""
    type: str = "paragraph"
    text: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    bbox: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    confidence: float = Field(
        default=1.0, sa_column=Column(Float, nullable=False, server_default=sa_text("1"))
    )
    source: str = "native"  # native | ocr | vlm | office
    ord: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=sa_text("0"))
    )
    attrs: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )


class Chunk(SQLModel, table=True):
    """An indexed passage. Child chunks are embedded; parents are returned."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document", "document_id"),
        Index("ix_chunks_parent", "parent_id"),
        Index("ix_chunks_classification", "classification"),
        # GIN over the generated tsvector: this is the sparse half of hybrid
        # retrieval, and the reason no separate search engine is needed.
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_tags", "tags", postgresql_using="gin"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("chunk"), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    parent_id: str | None = Field(default=None)
    ordinal: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=sa_text("0"))
    )
    text: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    token_count: int = 0

    page_from: int = 1
    page_to: int = 1
    bbox_union: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    block_ids: list[str] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]")
    )
    section_path: list[str] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]")
    )

    # Denormalised access-control columns — see the module docstring.
    classification: str = Classification.INTERNAL
    departments: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )
    tags: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )

    mean_confidence: float = Field(
        default=1.0, sa_column=Column(Float, nullable=False, server_default=sa_text("1"))
    )
    embedding_model: str = ""
    #: Point id in the vector store, so deletes stay in step across both stores.
    vector_id: str | None = Field(default=None, index=True)

    tsv: Any = Field(
        default=None,
        sa_column=Column(
            TSVECTOR,
            Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
            nullable=True,
        ),
    )
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class Dataset(SQLModel, table=True):
    """A tabular sheet extracted from a spreadsheet or CSV.

    Queried through DuckDB by the ``table.query`` tool, so the agent can compute
    over maintenance logs instead of trying to reason over serialised rows.
    """

    __tablename__ = "datasets"

    id: str = Field(default_factory=lambda: prefixed_id("dset"), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    sheet_name: str = ""
    row_count: int = 0
    column_spec: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    duckdb_path: str = ""


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_jobs"

    id: str = Field(default_factory=lambda: prefixed_id("job"), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    status: str = "queued"
    stage: str = "receive"
    progress: float = Field(
        default=0.0, sa_column=Column(Float, nullable=False, server_default=sa_text("0"))
    )
    stage_timings: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
