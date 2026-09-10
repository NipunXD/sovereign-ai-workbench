"""The vector store contract.

Two implementations satisfy it: Qdrant (default) and FAISS (portable fallback).
Both pass ``tests/unit/rag/test_vectorstore_contract.py``.

The critical requirement is that the access-control filter is applied *inside*
the search, not to its results. Over-fetching and filtering afterwards leaks
information through result counts and score distributions even when the text
itself is withheld, and it is the reason Qdrant is the default rather than a
simple flat index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """One indexed chunk: its vector plus the payload needed to filter and cite."""

    point_id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchHit:
    point_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        return str(self.payload.get("chunk_id", self.point_id))

    @property
    def doc_id(self) -> str:
        return str(self.payload.get("doc_id", ""))


@runtime_checkable
class VectorStore(Protocol):
    """A searchable store of chunk embeddings."""

    collection: str

    async def ensure_collection(self, dimensions: int) -> None:
        """Create the collection if absent. Must be idempotent."""
        ...

    async def upsert(self, points: list[VectorPoint]) -> int:
        """Insert or replace points. Returns the number written."""
        ...

    async def search(
        self,
        vector: list[float],
        *,
        limit: int = 40,
        access_filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Nearest neighbours, with the access filter applied inside the search.

        Implementations must never return a point the filter excludes, and must
        not satisfy the filter by trimming results afterwards.
        """
        ...

    async def delete_by_doc(self, doc_id: str) -> int:
        """Remove every point belonging to a document."""
        ...

    async def count(self) -> int: ...

    async def snapshot(self, path: str) -> str:
        """Write a portable copy of the index, for air-gapped transfer."""
        ...

    async def aclose(self) -> None: ...
