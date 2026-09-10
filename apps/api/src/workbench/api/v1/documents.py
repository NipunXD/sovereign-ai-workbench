"""Document upload, listing, viewing and deletion.

Upload streams the file, ingests it, and reports progress over SSE — a scanned
report takes long enough that a silent wait is indistinguishable from a hang.

Every read is filtered by the caller's clearance. A document above it does not
return 403, it returns 404: for a classified document the difference between
"exists but forbidden" and "does not exist" is itself information.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from workbench.api.deps import Audit, DbSession, require_permission
from workbench.api.sse import SSE_HEADERS, format_sse
from workbench.core.classification import Classification
from workbench.core.errors import NotFoundError, ValidationError
from workbench.core.hashing import digest_bytes
from workbench.core.ids import prefixed_id
from workbench.core.logging import get_logger
from workbench.db.models import (
    AuditAction,
    Chunk,
    DocStatus,
    Document,
    DocumentBlock,
    DocumentPage,
)
from workbench.security.rbac import Principal

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentSummary(BaseModel):
    id: str
    title: str
    filename: str
    doc_type: str
    mime: str
    page_count: int
    size_bytes: int
    classification: str
    departments: list[str]
    tags: list[str]
    status: str
    mean_confidence: float = 1.0
    degraded: bool = False
    chunk_count: int = 0
    created_at: str | None = None


class DocumentDetail(DocumentSummary):
    extraction_report: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _visible(principal: Principal) -> Any:
    """The clause restricting a query to what this principal may see."""
    return Document.classification.in_(principal.visible_classifications)


def _summary(document: Document, chunk_count: int = 0) -> DocumentSummary:
    report = document.extraction_report or {}
    return DocumentSummary(
        id=document.id,
        title=document.title,
        filename=document.filename,
        doc_type=document.doc_type,
        mime=document.mime,
        page_count=document.page_count,
        size_bytes=document.size_bytes,
        classification=document.classification,
        departments=list(document.departments),
        tags=list(document.tags),
        status=document.status,
        mean_confidence=float(report.get("mean_confidence", 1.0)),
        degraded=bool(report.get("degraded", False)),
        chunk_count=chunk_count,
        created_at=document.created_at.isoformat() if document.created_at else None,
    )


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
    doc_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DocumentSummary]:
    query = select(Document).where(Document.deleted_at.is_(None), _visible(principal))
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    if search:
        query = query.where(Document.title.ilike(f"%{search}%"))

    documents = list(
        (
            await session.execute(
                query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )
    if not documents:
        return []

    counts = dict(
        (
            await session.execute(
                select(Chunk.document_id, func.count(Chunk.id))
                .where(Chunk.document_id.in_([d.id for d in documents]))
                .group_by(Chunk.document_id)
            )
        ).all()
    )
    return [_summary(d, counts.get(d.id, 0)) for d in documents]


async def _load_visible(session: DbSession, document_id: str, principal: Principal) -> Document:
    """Fetch a document, or 404 if it is not visible.

    Deliberately not 403: telling an uncleared caller that a restricted document
    exists is itself a disclosure.
    """
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                _visible(principal),
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("No such document.")
    return document


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: str,
    session: DbSession,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
) -> DocumentDetail:
    document = await _load_visible(session, document_id, principal)
    count = (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.document_id == document.id))
    ).scalar_one()

    await audit.log(
        AuditAction.DOC_READ,
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        resource_type="document",
        resource_id=document.id,
        metadata={"classification": document.classification},
    )
    base = _summary(document, int(count))
    return DocumentDetail(
        **base.model_dump(),
        extraction_report=document.extraction_report or {},
        metadata=document.doc_metadata or {},
    )


@router.get("/{document_id}/pages/{page_no}/image")
async def page_image(
    document_id: str,
    page_no: int,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
) -> FileResponse:
    """The rendered page PNG the viewer displays."""
    await _load_visible(session, document_id, principal)
    page = (
        await session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
            )
        )
    ).scalar_one_or_none()
    if page is None or not page.image_path:
        raise NotFoundError("No image for that page.")

    from pathlib import Path

    path = Path(page.image_path)
    # Awaited off the loop: a stat on a slow or stalled mount would otherwise
    # block every other request in flight, not just this one.
    if not await anyio.to_thread.run_sync(path.is_file):
        raise NotFoundError("The page image is missing from storage.")
    return FileResponse(path, media_type="image/png")


@router.get("/{document_id}/pages/{page_no}/blocks")
async def page_blocks(
    document_id: str,
    page_no: int,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
) -> dict[str, Any]:
    """Laid-out blocks with normalised boxes, for the highlight overlay."""
    await _load_visible(session, document_id, principal)
    page = (
        await session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
            )
        )
    ).scalar_one_or_none()
    blocks = list(
        (
            await session.execute(
                select(DocumentBlock)
                .where(
                    DocumentBlock.document_id == document_id,
                    DocumentBlock.page_no == page_no,
                )
                .order_by(DocumentBlock.ord)
            )
        ).scalars()
    )
    return {
        "page_no": page_no,
        "width": page.width if page else 0,
        "height": page.height if page else 0,
        "mean_confidence": page.mean_confidence if page else 1.0,
        "blocks": [
            {
                "block_id": b.block_id,
                "type": b.type,
                "text": b.text,
                "bbox": b.bbox,
                "confidence": b.confidence,
                "source": b.source,
                "attrs": b.attrs,
            }
            for b in blocks
        ],
    }


@router.get("/{document_id}/chunks")
async def document_chunks(
    document_id: str,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
) -> list[dict[str, Any]]:
    """The indexed chunks. Exposed because 'why did it retrieve that?' is the
    first question anyone asks of a RAG system."""
    await _load_visible(session, document_id, principal)
    chunks = list(
        (
            await session.execute(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
            )
        ).scalars()
    )
    return [
        {
            "chunk_id": c.id,
            "parent_id": c.parent_id,
            "ordinal": c.ordinal,
            "text": c.text,
            "token_count": c.token_count,
            "page_from": c.page_from,
            "page_to": c.page_to,
            "section_path": c.section_path,
            "mean_confidence": c.mean_confidence,
            "indexed": c.vector_id is not None,
        }
        for c in chunks
    ]


@router.post("/upload")
async def upload(
    request: Request,
    session: DbSession,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("doc:ingest"))],
    file: UploadFile = File(...),
    classification: str = Form(default=Classification.INTERNAL),
    departments: str = Form(default=""),
    tags: str = Form(default=""),
    doc_type: str = Form(default="unknown"),
) -> StreamingResponse:
    """Ingest a document, streaming stage-by-stage progress."""
    if classification not in Classification.ORDER:
        raise ValidationError(f"'{classification}' is not a valid classification.")
    # A user cannot file a document above their own clearance: they would
    # immediately lose access to what they just uploaded, and more importantly
    # they would be asserting a sensitivity they are not cleared to judge.
    if Classification.outranks(classification, principal.clearance):
        raise ValidationError(
            f"You cannot classify a document as '{classification}'; your clearance "
            f"is '{principal.clearance}'."
        )

    data = await file.read()
    filename = file.filename or "upload"
    department_list = [d.strip() for d in departments.split(",") if d.strip()]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    pipeline = request.app.state.pipeline
    indexer = request.app.state.indexer
    if pipeline is None or indexer is None:
        raise ValidationError("Ingestion is unavailable; the vector store is not reachable.")

    sha = digest_bytes(data)
    existing = (
        await session.execute(select(Document).where(Document.sha256 == sha))
    ).scalar_one_or_none()
    document_id = prefixed_id("document")

    async def stream() -> AsyncIterator[str]:
        seq = 0

        if existing is not None:
            yield format_sse(
                "duplicate",
                {
                    "document_id": existing.id,
                    "title": existing.title,
                    "message": "This file is already indexed; nothing was re-ingested.",
                },
                seq=1,
            )
            return

        queue: list[tuple[str, dict[str, Any]]] = []

        async def progress(stage: Any, fraction: float, message: str) -> None:
            queue.append(
                (
                    "progress",
                    {"stage": stage.value, "progress": round(fraction, 3), "message": message},
                )
            )

        try:
            result = await pipeline.run(
                data=data, filename=filename, doc_id=document_id, progress=progress
            )
            for name, payload in queue:
                seq += 1
                yield format_sse(name, payload, seq=seq)

            if not result.ok or result.ir is None:
                seq += 1
                yield format_sse(
                    "error", {"message": "The document could not be processed."}, seq=seq
                )
                return

            from workbench.db.session import session_scope

            async with session_scope() as write:
                index_result = await indexer.index(
                    write,
                    ir=result.ir,
                    chunks=result.chunks,
                    sha256=result.sha256,
                    filename=filename,
                    owner_user_id=principal.user_id,
                    classification=classification,
                    departments=department_list,
                    tags=tag_list,
                    doc_type=doc_type,
                    blob_path=str(pipeline.blobs.path_for(result.sha256)),
                    size_bytes=len(data),
                )

            seq += 1
            yield format_sse(
                "complete",
                {
                    "document_id": index_result.document_id,
                    "title": result.ir.title,
                    "pages": index_result.pages,
                    "chunks": index_result.chunks,
                    "vectors": index_result.vectors,
                    "mean_confidence": round(result.ir.mean_confidence, 3),
                    "degraded": result.degraded,
                    "warnings": result.warnings,
                    "ocr_pages": result.ir.extraction_report.pages_ocr,
                    "vlm_pages": result.ir.extraction_report.pages_vlm_escalated,
                },
                seq=seq,
            )
        except Exception as exc:
            log.exception("upload_failed", filename=filename, error=str(exc))
            for name, payload in queue:
                seq += 1
                yield format_sse(name, payload, seq=seq)
            seq += 1
            yield format_sse("error", {"message": str(exc)[:300]}, seq=seq)

    await audit.log(
        AuditAction.DOC_INGEST,
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        actor_roles=sorted(principal.roles),
        resource_type="document",
        resource_id=document_id,
        metadata={
            "filename": filename,
            "classification": classification,
            "size_bytes": len(data),
        },
    )
    await session.commit()
    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    session: DbSession,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("doc:delete"))],
) -> dict[str, str]:
    """Soft-delete a document and purge its vectors.

    The blob is kept: an audit trail referring to a document whose content no
    longer exists cannot be reviewed.
    """
    document = await _load_visible(session, document_id, principal)
    indexer = request.app.state.indexer
    if indexer is not None:
        await indexer.vector_store.delete_by_doc(document_id)

    from workbench.core.clock import now

    document.deleted_at = now()
    document.status = DocStatus.PENDING
    await session.execute(Chunk.__table__.delete().where(Chunk.document_id == document_id))
    await audit.log(
        AuditAction.DOC_DELETE,
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        resource_type="document",
        resource_id=document_id,
        metadata={"title": document.title},
    )
    return {"status": "deleted", "document_id": document_id}
