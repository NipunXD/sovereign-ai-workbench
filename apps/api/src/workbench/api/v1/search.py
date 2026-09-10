"""Retrieval, exposed directly.

Separate from chat on purpose. "Why did it answer that?" is the first question
anyone asks of a RAG system, and being able to see exactly what retrieval
returns — with scores, which retriever found it, and the OCR confidence of the
source — answers it without involving a model at all.

It is also how the confidentiality guarantee is demonstrated: run the same query
as two users and watch the result sets differ.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from workbench.api.deps import Audit, require_permission
from workbench.core.errors import RetrievalError
from workbench.db.models import AuditAction
from workbench.security.rbac import AccessFilter, Principal

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)


class SearchHitOut(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    page_from: int
    page_to: int
    text: str
    score: float
    retrieval_method: str
    confidence: float
    section_path: list[str]
    bbox: dict[str, float]


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitOut]
    #: What the caller was allowed to see, so a thin result set is explicable.
    access_filter: str
    clearance: str


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    request: Request,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("doc:read"))],
) -> SearchResponse:
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        raise RetrievalError("Search is unavailable; the vector store is not reachable.")

    hits = await retriever.search(payload.query, principal, k=payload.k)

    await audit.log(
        AuditAction.RAG_SEARCH,
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        actor_roles=sorted(principal.roles),
        resource_type="search",
        metadata={
            "query": payload.query[:200],
            "results": len(hits),
            "clearance": principal.clearance,
        },
    )

    return SearchResponse(
        query=payload.query,
        access_filter=AccessFilter.for_principal(principal).describe(),
        clearance=principal.clearance,
        hits=[
            SearchHitOut(
                chunk_id=h.chunk_id,
                doc_id=h.doc_id,
                doc_title=h.doc_title,
                doc_type=h.doc_type,
                page_from=h.page_from,
                page_to=h.page_to or h.page_from,
                text=h.text,
                score=round(h.score, 5),
                retrieval_method=h.retrieval_method,
                confidence=round(h.confidence, 3),
                section_path=h.section_path,
                bbox=h.bbox.as_dict(),
            )
            for h in hits
        ],
    )
