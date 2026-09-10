"""Retrieval must never surface a chunk above the caller's clearance.

This is the confidentiality guarantee the whole system rests on. The filter is
applied inside the vector search rather than to its results, so these tests
assert on what the store returns, not on what a later layer trims.

Marked `qdrant` because it needs the real store — a fake would not exercise the
thing that actually matters, which is that Qdrant's own filter is constructed
correctly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from workbench.providers.registry import ModelRegistry
from workbench.rag.embedder import Embedder
from workbench.rag.vectorstore.base import VectorPoint
from workbench.rag.vectorstore.qdrant_store import QdrantStore
from workbench.security.rbac import AccessFilter, Principal, RbacConfig

pytestmark = pytest.mark.integration

QDRANT_URL = os.environ.get("WORKBENCH_QDRANT_URL", "http://localhost:6333")

#: chunk_id, text, classification, departments
CORPUS = [
    ("c_pub", "Depressurisation rate shall not exceed 2 bar per minute.", "public", []),
    (
        "c_int",
        "Vessel V-1201 shell thickness measured 9.2 mm at CML-4.",
        "internal",
        ["inspection"],
    ),
    ("c_con", "Turnaround budget for the CDU is 14.2 crore.", "confidential", ["finance"]),
    ("c_res", "Catalyst formulation ratio is 3:1 nickel to molybdenum.", "restricted", ["process"]),
]


@pytest.fixture
async def store(manifest_path: Path) -> AsyncIterator[tuple[QdrantStore, Embedder]]:
    """A throwaway collection loaded with the fixture corpus."""
    registry = ModelRegistry(manifest_path)
    info = registry.get_model("embed.primary")
    embedder = Embedder(registry.provider_for("embed.primary"), info)

    collection = f"test_rbac_{os.getpid()}"
    vectors = QdrantStore(QDRANT_URL, collection)
    try:
        await vectors.ensure_collection(embedder.dimensions)
    except Exception as exc:
        pytest.skip(f"Qdrant unavailable at {QDRANT_URL}: {exc}")

    result = await embedder.embed([text for _, text, _, _ in CORPUS])
    await vectors.upsert(
        [
            VectorPoint(
                point_id=chunk_id,
                vector=vector,
                payload={
                    "chunk_id": chunk_id,
                    "doc_id": f"doc_{chunk_id}",
                    "text": text,
                    "classification": classification,
                    "departments": departments,
                },
            )
            for (chunk_id, text, classification, departments), vector in zip(
                CORPUS, result.vectors, strict=True
            )
        ]
    )
    try:
        yield vectors, embedder
    finally:
        for chunk_id, *_ in CORPUS:
            await vectors.delete_by_doc(f"doc_{chunk_id}")
        await vectors.aclose()
        await registry.aclose()


def make_principal(rbac: RbacConfig, role: str) -> Principal:
    return Principal(
        user_id=f"u_{role}",
        username=role,
        roles=frozenset({role}),
        permissions=rbac.permissions_for({role}),
        clearance=rbac.clearance_for({role}),
    )


async def search_as(
    store: tuple[QdrantStore, Embedder], rbac: RbacConfig, role: str, query: str
) -> list[str]:
    vectors, embedder = store
    principal = make_principal(rbac, role)
    hits = await vectors.search(
        await embedder.embed_query(query),
        limit=10,
        access_filter=AccessFilter.for_principal(principal).to_qdrant(),
    )
    return [hit.chunk_id for hit in hits]


@pytest.fixture
def rbac(rbac_config_path: Path) -> RbacConfig:
    return RbacConfig.load(rbac_config_path)


@pytest.mark.parametrize(
    ("role", "visible"),
    [
        ("viewer", {"c_pub", "c_int"}),
        ("auditor", {"c_pub", "c_int"}),
        ("engineer", {"c_pub", "c_int", "c_con"}),
        ("approver", {"c_pub", "c_int", "c_con"}),
        ("senior_engineer", {"c_pub", "c_int", "c_con", "c_res"}),
        ("admin", {"c_pub", "c_int", "c_con", "c_res"}),
    ],
)
async def test_each_role_sees_exactly_its_clearance(
    store: tuple[QdrantStore, Embedder], rbac: RbacConfig, role: str, visible: set[str]
) -> None:
    """A broad query should return everything the role may see, and nothing more."""
    returned = set(await search_as(store, rbac, role, "refinery vessel process information"))
    assert returned == visible, f"{role} saw {returned}, expected {visible}"


async def test_targeted_query_cannot_surface_a_restricted_chunk(
    store: tuple[QdrantStore, Embedder], rbac: RbacConfig
) -> None:
    """The adversarial case: ask directly for the secret.

    A user who knows exactly what to search for must still get nothing. This is
    where retrieve-then-filter would leak — the chunk would be found, scored,
    and only then withheld, which is observable.
    """
    returned = await search_as(
        store, rbac, "viewer", "catalyst formulation nickel molybdenum ratio"
    )
    assert "c_res" not in returned

    # The same query as a cleared user must find it, or the test proves nothing
    # about filtering — only that retrieval is broken.
    cleared = await search_as(
        store, rbac, "senior_engineer", "catalyst formulation nickel molybdenum ratio"
    )
    assert "c_res" in cleared


async def test_department_scoping_narrows_further(
    store: tuple[QdrantStore, Embedder], rbac: RbacConfig
) -> None:
    """Clearance and department are independent restrictions."""
    vectors, embedder = store
    scoped = Principal(
        user_id="u_dept",
        username="engineer",
        permissions=rbac.permissions_for({"engineer"}),
        clearance="confidential",
        departments=frozenset({"inspection"}),
    )
    hits = await vectors.search(
        await embedder.embed_query("refinery information"),
        limit=10,
        access_filter=AccessFilter.for_principal(scoped).to_qdrant(),
    )
    returned = {hit.chunk_id for hit in hits}
    assert "c_int" in returned, "own-department chunk should be visible"
    assert "c_con" not in returned, "other-department chunk should be hidden"
    assert "c_pub" in returned, "chunk with no department is plant-wide"
