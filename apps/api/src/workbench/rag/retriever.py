"""Hybrid retrieval.

Dense and sparse search fail in opposite directions. An embedding blurs exact
tokens — ``V-1201`` and ``V-1202`` land next to each other, which is precisely
wrong for equipment tags. Keyword search finds the tag exactly but misses "the
vessel" entirely. Running both and fusing by rank gets the strengths of each.

The access filter is built once and applied to *both* halves, so the two cannot
disagree about what a user may see.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workbench.core.ir import BBox
from workbench.core.logging import get_logger
from workbench.rag.citations import EvidenceItem
from workbench.rag.embedder import Embedder
from workbench.rag.fusion import apply_confidence_penalty, reciprocal_rank_fusion
from workbench.rag.vectorstore.base import VectorStore
from workbench.security.rbac import AccessFilter, Principal

log = get_logger(__name__)


class HybridRetriever:
    """Dense + sparse retrieval fused by reciprocal rank."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        vector_store: VectorStore,
        #: Optional: without it, retrieval is dense-only. Guarded below.
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        dense_k: int = 40,
        sparse_k: int = 40,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        sparse_weight: float = 0.8,
        final_k: int = 8,
        confidence_penalty: float = 0.15,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.session_factory = session_factory
        self.dense_k = dense_k
        self.sparse_k = sparse_k
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.final_k = final_k
        self.confidence_penalty = confidence_penalty

    async def search(
        self, query: str, principal: Principal, *, k: int | None = None
    ) -> list[EvidenceItem]:
        """Retrieve passages this principal is allowed to see."""
        started = time.perf_counter()
        limit = k or self.final_k
        access = AccessFilter.for_principal(principal)

        dense_hits = await self._dense(query, access)
        sparse_hits = await self._sparse(query, principal)

        payloads: dict[str, dict[str, Any]] = {}
        for hit in dense_hits:
            payloads.setdefault(hit["chunk_id"], hit)
        for hit in sparse_hits:
            payloads.setdefault(hit["chunk_id"], hit)

        fused = reciprocal_rank_fusion(
            {
                "dense": [h["chunk_id"] for h in dense_hits],
                "sparse": [h["chunk_id"] for h in sparse_hits],
            },
            k=self.rrf_k,
            weights={"dense": self.dense_weight, "sparse": self.sparse_weight},
        )
        fused = apply_confidence_penalty(
            fused,
            {cid: float(p.get("mean_confidence", 1.0)) for cid, p in payloads.items()},
            penalty=self.confidence_penalty,
        )

        evidence = [
            self._to_evidence(hit.chunk_id, payloads[hit.chunk_id], hit.score, hit.method)
            for hit in fused[:limit]
            if hit.chunk_id in payloads
        ]

        log.info(
            "retrieval_complete",
            query=query[:120],
            dense=len(dense_hits),
            sparse=len(sparse_hits),
            returned=len(evidence),
            latency_ms=int((time.perf_counter() - started) * 1000),
            access=access.describe(),
        )
        return evidence

    async def _dense(self, query: str, access: AccessFilter) -> list[dict[str, Any]]:
        try:
            vector = await self.embedder.embed_query(query)
        except Exception as exc:
            log.warning("dense_embed_failed", error=str(exc))
            return []
        if not vector:
            return []
        try:
            hits = await self.vector_store.search(
                vector, limit=self.dense_k, access_filter=access.to_qdrant()
            )
        except Exception as exc:
            # Degrading to sparse-only beats failing the request outright.
            log.warning("dense_search_failed", error=str(exc))
            return []
        return [{"chunk_id": h.chunk_id, **h.payload, "dense_score": h.score} for h in hits]

    @staticmethod
    def _tsquery(query: str) -> str:
        """Build an OR-of-terms tsquery from a natural question.

        ``websearch_to_tsquery`` and ``plainto_tsquery`` both AND their terms, so
        "What is the depressurisation rate limit for V-1201?" matches only a
        chunk containing every one of those words — which is essentially never,
        and it silently reduced hybrid search to dense-only.

        Terms are OR-ed instead, which is what a BM25-style ranker expects: the
        scoring function decides relevance, not a hard conjunction. Equipment
        tags are kept whole because "V-1201" is the single most discriminating
        token a refinery query can contain.
        """
        import re

        # Keep tags intact; split everything else on non-word characters.
        tags = re.findall(r"[A-Za-z]{1,4}-\d{1,5}[A-Za-z]?", query)
        remainder = re.sub(r"[A-Za-z]{1,4}-\d{1,5}[A-Za-z]?", " ", query)
        words = re.findall(r"[A-Za-z0-9]{3,}", remainder)

        stop = {
            "the",
            "and",
            "for",
            "what",
            "which",
            "with",
            "from",
            "that",
            "this",
            "are",
            "was",
            "were",
            "has",
            "have",
            "does",
            "did",
            "how",
            "why",
            "when",
            "where",
            "who",
            "whom",
            "into",
            "onto",
            "about",
            "any",
            "all",
        }
        terms = [*tags, *(w for w in words if w.lower() not in stop)]
        if not terms:
            terms = words or tags
        if not terms:
            return ""
        # Quote each term so punctuation inside a tag cannot break the syntax.
        return " | ".join(f"'{t}'" for t in dict.fromkeys(terms))

    async def _sparse(self, query: str, principal: Principal) -> list[dict[str, Any]]:
        """Postgres full-text search, with the same access clause in SQL.

        This is where an exact equipment tag is found — the thing a dense
        embedding is worst at, because V-1201 and V-1202 embed almost
        identically.
        """
        if self.session_factory is None:
            return []
        tsquery = self._tsquery(query)
        if not tsquery:
            return []

        sql = sa_text(
            """
            SELECT c.id AS chunk_id, c.text, c.document_id, c.page_from, c.page_to,
                   c.section_path, c.bbox_union, c.mean_confidence, c.parent_id,
                   d.title AS doc_title, d.doc_type,
                   ts_rank_cd(c.tsv, to_tsquery('english', :q)) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.tsv @@ to_tsquery('english', :q)
              AND d.deleted_at IS NULL
              AND c.classification = ANY(:classifications)
              AND (
                    cardinality(c.departments) = 0
                    OR :no_department_filter
                    OR c.departments && :departments
                  )
            ORDER BY rank DESC
            LIMIT :limit
            """
        )
        try:
            async with self.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            sql,
                            {
                                "q": tsquery,
                                "classifications": principal.visible_classifications,
                                "departments": sorted(principal.departments) or [""],
                                "no_department_filter": not principal.departments,
                                "limit": self.sparse_k,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
        except Exception as exc:
            log.warning("sparse_search_failed", error=str(exc))
            return []

        return [
            {
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "doc_id": row["document_id"],
                "doc_title": row["doc_title"],
                "doc_type": row["doc_type"],
                "page_from": row["page_from"],
                "page_to": row["page_to"],
                "section_path": row["section_path"] or [],
                "bbox_union": row["bbox_union"] or {},
                "mean_confidence": row["mean_confidence"],
                "parent_id": row["parent_id"],
                "sparse_score": float(row["rank"]),
            }
            for row in rows
        ]

    @staticmethod
    def _to_evidence(
        chunk_id: str, payload: dict[str, Any], score: float, method: str
    ) -> EvidenceItem:
        bbox_raw = payload.get("bbox_union") or {}
        return EvidenceItem(
            chunk_id=chunk_id,
            text=str(payload.get("text", "")),
            doc_id=str(payload.get("doc_id", "")),
            doc_title=str(payload.get("doc_title", "")),
            doc_type=str(payload.get("doc_type", "")),
            page_from=int(payload.get("page_from", 1) or 1),
            page_to=int(payload.get("page_to", 0) or 0),
            bbox=BBox(**bbox_raw) if bbox_raw else BBox(),
            section_path=list(payload.get("section_path") or []),
            score=score,
            retrieval_method=method,
            confidence=float(payload.get("mean_confidence", 1.0) or 1.0),
            parent_id=payload.get("parent_id"),
        )
