"""Retrieval quality against the seed corpus.

Cases are labelled by *document plus a phrase that must appear in a retrieved
passage*, not by chunk id. Chunk ids are ULIDs regenerated on every ingest, so a
dataset keyed on them would need relabelling after any change to chunking — and
would silently stop measuring anything the moment it drifted. Labelling by
content also states the intent better: the question is whether the passage that
answers the query was found.

``expect_doc`` is resolved through the seed manifest into the document title,
which is what retrieval actually returns, and an unresolvable label aborts the
suite. The first version substring-matched the label against the title instead.
That looked equivalent and was not: the manifest ids are ``INSP-2029-V1201``
and ``MAINT-LOG-2029Q1`` while the dataset had been written with ``INSP-2029``
and ``MAINT-LOG``, so ten of twenty cases could never match no matter how well
retrieval performed. The suite reported recall@10 = 0.50 and named the missing
passages in its output; all ten were in fact retrieved, several at rank 1. Only
the cases whose id happens to appear verbatim in the title (``SOP-4412``) were
measuring anything. Resolving up front turns that silent 50% into a loud error.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.metrics import ndcg_at_k, percentile, reciprocal_rank
from evals.harness.registry import CaseResult, SuiteResult, suite

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "retrieval_qa.jsonl"
MANIFEST = REPO_ROOT / "data" / "seed" / "ground_truth" / "doc_manifest.json"

#: The admin sees everything, so retrieval quality is measured without the
#: access filter narrowing the corpus. Confidentiality is tested separately, in
#: the RBAC integration suite — conflating the two would let a filtering bug
#: masquerade as a retrieval regression.
EVAL_ROLE = "admin"


def _titles_by_doc_id() -> dict[str, str]:
    """Map manifest doc ids to the titles retrieval returns."""
    return {entry["doc_id"]: entry["title"] for entry in json.loads(MANIFEST.read_text())}


@suite("retrieval")
async def run() -> SuiteResult:
    import time

    from workbench.db.session import get_session_factory, init_engine
    from workbench.providers.registry import ModelRegistry
    from workbench.rag.embedder import Embedder
    from workbench.rag.retriever import HybridRetriever
    from workbench.rag.vectorstore.qdrant_store import QdrantStore
    from workbench.security.rbac import Principal, RbacConfig
    from workbench.settings import get_settings

    settings = get_settings()
    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]

    # Resolve every label before running anything. A label that names no
    # document is a broken dataset, and a broken dataset must not be reportable
    # as a retrieval score.
    titles = _titles_by_doc_id()
    unresolved = sorted({c["expect_doc"] for c in cases if c["expect_doc"] not in titles})
    if unresolved:
        return SuiteResult(
            suite="retrieval",
            skipped=(
                f"dataset labels name no document in the manifest: {', '.join(unresolved)} "
                f"(known ids: {', '.join(sorted(titles))})"
            ),
        )

    try:
        init_engine(settings)
        registry = ModelRegistry(settings.models_manifest)
        model = registry.get_model("embed.primary")
        embedder = Embedder(registry.provider_for("embed.primary"), model)
        store = QdrantStore(settings.qdrant_url, embedder.collection)
        if await store.count() == 0:
            return SuiteResult(
                suite="retrieval",
                skipped="the corpus is not indexed — run `make seed-corpus`",
            )
        retriever = HybridRetriever(
            embedder=embedder, vector_store=store, session_factory=get_session_factory()
        )
    except Exception as exc:
        return SuiteResult(suite="retrieval", skipped=f"retrieval unavailable: {exc}")

    rbac = RbacConfig.load(settings.rbac_config)
    principal = Principal(
        user_id="eval",
        username="eval",
        roles=frozenset({EVAL_ROLE}),
        permissions=rbac.permissions_for({EVAL_ROLE}),
        clearance=rbac.clearance_for({EVAL_ROLE}),
    )

    results: list[CaseResult] = []
    recalls_5: list[float] = []
    recalls_10: list[float] = []
    rrs: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    hybrid_hits = 0
    total_hits = 0

    for case in cases:
        started = time.perf_counter()
        hits = await retriever.search(case["query"], principal, k=10)
        latencies.append(time.perf_counter() - started)

        # A hit is relevant when it comes from the expected document *and*
        # contains the phrase — the document alone is too coarse when two
        # inspection reports differ only by year.
        phrase = case["expect_phrase"].lower()
        expected_title = titles[case["expect_doc"]].lower()
        ranked: list[str] = []
        relevant: set[str] = set()
        for index, hit in enumerate(hits):
            key = f"{index}"
            ranked.append(key)
            if (hit.doc_title or "").lower() == expected_title and phrase in hit.text.lower():
                relevant.add(key)

        for hit in hits:
            total_hits += 1
            if hit.retrieval_method == "hybrid":
                hybrid_hits += 1

        found = bool(relevant)
        recalls_5.append(1.0 if any(int(k) < 5 for k in relevant) else 0.0)
        recalls_10.append(1.0 if found else 0.0)
        rrs.append(reciprocal_rank(ranked, relevant))
        # Each case has exactly one gold passage, so "not found" is a zero, not
        # the vacuous 1.0 an empty gold set used to produce.
        ndcgs.append(ndcg_at_k(ranked, relevant, 10) if found else 0.0)

        results.append(
            CaseResult(
                case_id=case["id"],
                passed=found,
                metrics={"rank": min((int(k) for k in relevant), default=-1) + 1},
                detail=(
                    ""
                    if found
                    else f"'{case['expect_phrase']}' from {case['expect_doc']} not in the top 10"
                ),
                expected=case["expect_doc"],
                actual=[h.doc_title for h in hits[:3]],
            )
        )

    await store.aclose()
    await registry.aclose()

    count = len(cases) or 1
    return SuiteResult(
        suite="retrieval",
        cases=results,
        metrics={
            "recall_at_5": sum(recalls_5) / count,
            "recall_at_10": sum(recalls_10) / count,
            "mrr": sum(rrs) / count,
            "ndcg_at_10": sum(ndcgs) / count,
            "hybrid_share": hybrid_hits / total_hits if total_hits else 0.0,
            "p95_latency_s": percentile(latencies, 0.95),
            "cases": float(count),
        },
        thresholds={
            "recall_at_10": (">=", 0.85),
            "mrr": (">=", 0.60),
            "ndcg_at_10": (">=", 0.65),
        },
    )
