"""Persisting an ingested document.

Writes the document, its pages, blocks and chunks to Postgres, and the chunk
vectors to Qdrant. The two stores have to agree: a chunk row without a vector is
invisible to dense search, and a vector without a row cannot be cited because
there is nothing to resolve a page number or bounding box from.

So the order is deliberate — relational rows first, vectors second, and a failed
vector upsert rolls the rows back. A half-indexed document that silently returns
partial results is worse than an ingestion that failed loudly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from workbench.core.classification import Classification
from workbench.core.logging import get_logger
from workbench.db.models import (
    Chunk,
    Dataset,
    DocStatus,
    Document,
    DocumentBlock,
    DocumentPage,
)
from workbench.ingest.ir import DocumentIR
from workbench.rag.embedder import Embedder
from workbench.rag.vectorstore.base import VectorPoint, VectorStore

log = get_logger(__name__)


@dataclass
class IndexResult:
    document_id: str
    pages: int = 0
    blocks: int = 0
    chunks: int = 0
    vectors: int = 0
    duration_ms: int = 0


class DocumentIndexer:
    """Writes an ingested document into both stores."""

    def __init__(self, *, embedder: Embedder, vector_store: VectorStore) -> None:
        self.embedder = embedder
        self.vector_store = vector_store

    async def index(
        self,
        session: AsyncSession,
        *,
        ir: DocumentIR,
        chunks: list[Any],
        sha256: str,
        filename: str,
        owner_user_id: str | None,
        classification: str = Classification.INTERNAL,
        departments: list[str] | None = None,
        tags: list[str] | None = None,
        doc_type: str = "unknown",
        blob_path: str = "",
        size_bytes: int = 0,
        title: str | None = None,
    ) -> IndexResult:
        started = time.perf_counter()
        departments = departments or []
        tags = tags or []

        document = Document(
            id=ir.doc_id,
            sha256=sha256,
            # A caller-supplied title wins: a scanned document carries no
            # metadata, so the extractor can only fall back to the filename.
            title=title or ir.title or filename,
            filename=filename,
            mime=ir.mime,
            doc_type=doc_type,
            page_count=ir.page_count,
            size_bytes=size_bytes,
            classification=classification,
            departments=departments,
            tags=tags,
            owner_user_id=owner_user_id,
            status=DocStatus.PROCESSING,
            blob_path=blob_path,
            doc_metadata=ir.metadata,
            extraction_report=ir.extraction_report.model_dump(mode="json"),
        )
        session.add(document)
        await session.flush()

        for page in ir.pages:
            session.add(
                DocumentPage(
                    document_id=document.id,
                    page_no=page.page_no,
                    width=page.width,
                    height=page.height,
                    image_path=page.image_ref,
                    mean_confidence=page.mean_confidence,
                    ocr_engine=ir.extraction_report.extractor,
                )
            )
            for block in page.blocks:
                session.add(
                    DocumentBlock(
                        document_id=document.id,
                        page_no=block.page_no,
                        block_id=block.block_id,
                        type=block.type.value,
                        text=block.text,
                        bbox=block.bbox.as_dict(),
                        confidence=block.confidence,
                        source=block.source.value,
                        ord=block.order,
                        attrs=block.attrs,
                    )
                )

        for dataset in ir.datasets:
            session.add(
                Dataset(
                    document_id=document.id,
                    sheet_name=dataset.sheet_name,
                    row_count=dataset.row_count,
                    column_spec={"columns": dataset.columns},
                    duckdb_path=dataset.path,
                )
            )

        # Only child chunks are embedded and searched; parents are returned as
        # context once a child matches, so embedding both would double the cost
        # and let the same passage match twice.
        searchable = [c for c in chunks if not c.is_parent]
        vectors: list[list[float]] = []
        if searchable:
            result = await self.embedder.embed([c.contextualised_text for c in searchable])
            vectors = result.vectors
            if result.truncated:
                log.warning(
                    "chunks_truncated_for_embedding",
                    document=document.id,
                    count=len(result.truncated),
                )

        vector_by_chunk = {c.chunk_id: v for c, v in zip(searchable, vectors, strict=False)}

        for chunk in chunks:
            session.add(
                Chunk(
                    id=chunk.chunk_id,
                    document_id=document.id,
                    parent_id=chunk.parent_id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    bbox_union=chunk.attrs.get("bbox_union", {}),
                    block_ids=chunk.block_ids,
                    section_path=chunk.section_path,
                    # Denormalised from the document so the retrieval filter can
                    # run without a join, in both Postgres and Qdrant.
                    classification=classification,
                    departments=departments,
                    tags=tags,
                    mean_confidence=chunk.mean_confidence,
                    embedding_model=self.embedder.model.physical_id,
                    vector_id=chunk.chunk_id if chunk.chunk_id in vector_by_chunk else None,
                )
            )
        await session.flush()

        points = [
            VectorPoint(
                point_id=chunk.chunk_id,
                vector=vector_by_chunk[chunk.chunk_id],
                payload={
                    "chunk_id": chunk.chunk_id,
                    "doc_id": document.id,
                    "doc_title": document.title,
                    "doc_type": doc_type,
                    "text": chunk.text,
                    "page_from": chunk.page_from,
                    "page_to": chunk.page_to,
                    "section_path": chunk.section_path,
                    "bbox_union": chunk.attrs.get("bbox_union", {}),
                    "mean_confidence": chunk.mean_confidence,
                    "parent_id": chunk.parent_id,
                    "classification": classification,
                    "departments": departments,
                    "tags": tags,
                },
            )
            for chunk in searchable
            if chunk.chunk_id in vector_by_chunk
        ]

        if points:
            try:
                await self.vector_store.ensure_collection(self.embedder.dimensions)
                await self.vector_store.upsert(points)
            except Exception:
                # Roll the relational rows back so the two stores cannot
                # disagree. A document indexed in one but not the other returns
                # partial results with no indication anything is missing.
                await session.rollback()
                log.error("vector_upsert_failed_rolled_back", document=document.id)
                raise

        document.status = DocStatus.READY
        from workbench.core.clock import now

        document.indexed_at = now()
        await session.flush()

        return IndexResult(
            document_id=document.id,
            pages=len(ir.pages),
            blocks=len(ir.blocks),
            chunks=len(chunks),
            vectors=len(points),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def remove(self, session: AsyncSession, document_id: str) -> None:
        """Delete a document from both stores, vectors first.

        Vectors go first because an orphaned vector is retrievable and citable,
        while an orphaned row is merely dead weight.
        """
        await self.vector_store.delete_by_doc(document_id)
        for model in (Chunk, DocumentBlock, DocumentPage, Dataset):
            await session.execute(delete(model).where(model.document_id == document_id))
        await session.execute(delete(Document).where(Document.id == document_id))

    async def find_by_hash(self, session: AsyncSession, sha256: str) -> Document | None:
        return (
            await session.execute(select(Document).where(Document.sha256 == sha256))
        ).scalar_one_or_none()
