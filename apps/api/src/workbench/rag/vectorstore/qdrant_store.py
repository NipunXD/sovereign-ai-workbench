"""Qdrant vector store.

Chosen as the default because the access-control clause runs *inside* the ANN
search. A flat index would force retrieve-then-filter, which leaks through
result counts and score distributions, and makes deletes and re-indexing awkward
as documents are re-uploaded.
"""

from __future__ import annotations

from typing import Any

from qdrant_client import AsyncQdrantClient, models

from workbench.core.errors import RetrievalError
from workbench.core.logging import get_logger
from workbench.rag.vectorstore.base import SearchHit, VectorPoint

log = get_logger(__name__)


class QdrantStore:
    """Vector storage and filtered search backed by Qdrant."""

    def __init__(self, url: str, collection: str, *, timeout_s: float = 30.0) -> None:
        self.collection = collection
        self._client = AsyncQdrantClient(url=url, timeout=int(timeout_s))

    async def ensure_collection(self, dimensions: int) -> None:
        """Create the collection and its payload indexes, idempotently."""
        try:
            if await self._client.collection_exists(self.collection):
                info = await self._client.get_collection(self.collection)
                existing = info.config.params.vectors.size  # type: ignore[union-attr]
                if existing != dimensions:
                    # Silently writing 768-d vectors into a 1024-d collection is
                    # not possible; making the mismatch explicit points at the
                    # actual cause, which is an embedding model change.
                    raise RetrievalError(
                        f"collection '{self.collection}' holds {existing}-dimensional "
                        f"vectors but the embedding model produces {dimensions}. The "
                        f"embedding model changed; re-index into a new collection."
                    )
                return

            await self._client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=dimensions, distance=models.Distance.COSINE
                ),
            )
            # Payload indexes make the access filter cheap. Without them Qdrant
            # scans payloads, and the filter dominates query time.
            for field, schema in (
                ("classification", models.PayloadSchemaType.KEYWORD),
                ("departments", models.PayloadSchemaType.KEYWORD),
                ("doc_id", models.PayloadSchemaType.KEYWORD),
                ("tags", models.PayloadSchemaType.KEYWORD),
            ):
                await self._client.create_payload_index(
                    collection_name=self.collection, field_name=field, field_schema=schema
                )
            log.info("qdrant_collection_created", collection=self.collection, dimensions=dimensions)
        except RetrievalError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"could not prepare collection '{self.collection}': {exc}") from exc

    async def upsert(self, points: list[VectorPoint]) -> int:
        if not points:
            return 0
        try:
            await self._client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(
                        id=self._point_id(p.point_id), vector=p.vector, payload=p.payload
                    )
                    for p in points
                ],
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"vector upsert failed: {exc}") from exc
        return len(points)

    async def search(
        self,
        vector: list[float],
        *,
        limit: int = 40,
        access_filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Nearest neighbours with the access filter applied inside the search."""
        try:
            response = await self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=limit,
                query_filter=self._to_filter(access_filter),
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"vector search failed: {exc}") from exc

        return [
            SearchHit(point_id=str(p.id), score=float(p.score), payload=dict(p.payload or {}))
            for p in response.points
        ]

    async def delete_by_doc(self, doc_id: str) -> int:
        """Purge a document's vectors.

        Kept in step with the relational delete: a chunk whose document is gone
        would otherwise still be retrievable and citable.
        """
        try:
            await self._client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="doc_id", match=models.MatchValue(value=doc_id)
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"vector delete failed: {exc}") from exc
        return 1

    async def count(self) -> int:
        try:
            result = await self._client.count(self.collection, exact=True)
            return int(result.count)
        except Exception:  # noqa: BLE001
            return 0

    async def snapshot(self, path: str) -> str:
        """Ask Qdrant for a snapshot file, for carrying to an air-gapped site."""
        try:
            info = await self._client.create_snapshot(collection_name=self.collection)
            return str(getattr(info, "name", path))
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"snapshot failed: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------- internals
    @staticmethod
    def _point_id(chunk_id: str) -> str:
        """Qdrant point ids must be a UUID or an unsigned integer.

        Chunk ids are prefixed ULIDs, so they are mapped deterministically onto
        a UUID — deterministically, so re-ingesting the same chunk replaces its
        point instead of creating a duplicate.
        """
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"workbench/chunk/{chunk_id}"))

    @staticmethod
    def _to_filter(access_filter: dict[str, Any] | None) -> models.Filter | None:
        """Translate an AccessFilter dict into Qdrant's filter model."""
        if not access_filter:
            return None

        def condition(clause: dict[str, Any]) -> Any:
            if "is_empty" in clause:
                return models.IsEmptyCondition(
                    is_empty=models.PayloadField(key=clause["is_empty"]["key"])
                )
            if "should" in clause:
                return models.Filter(should=[condition(c) for c in clause["should"]])
            key = clause["key"]
            match = clause["match"]
            if "any" in match:
                return models.FieldCondition(key=key, match=models.MatchAny(any=match["any"]))
            return models.FieldCondition(key=key, match=models.MatchValue(value=match["value"]))

        return models.Filter(
            must=[condition(c) for c in access_filter.get("must", [])] or None,
            must_not=[condition(c) for c in access_filter.get("must_not", [])] or None,
        )
