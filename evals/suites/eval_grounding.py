"""Whether answers stay inside the evidence.

The number that matters most here is the refusal rate on unanswerable
questions. A system that answers everything is easy to build and useless in a
refinery: the failure mode is a confident, fluent, wrong figure about a vessel,
and no amount of retrieval quality compensates for it.

Runs a real agent against real models, so it is slow. It is skipped rather than
faked when no backend is reachable — a grounding number produced against a mock
would measure nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.metrics import percentile
from evals.harness.registry import CaseResult, SuiteResult, suite

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "grounding.jsonl"


@suite("grounding")
async def run() -> SuiteResult:
    import time

    from workbench.agent.refusal import is_refusal
    from workbench.agent.runner import AgentRunner
    from workbench.agent.state import Budget
    from workbench.api.sse import EventName
    from workbench.db.session import get_session_factory, init_engine
    from workbench.providers.registry import ModelRegistry
    from workbench.providers.residency import ResidencyManager
    from workbench.rag.embedder import Embedder
    from workbench.rag.retriever import HybridRetriever
    from workbench.rag.vectorstore.qdrant_store import QdrantStore
    from workbench.router.router import ModelRouter
    from workbench.security.rbac import Principal, RbacConfig
    from workbench.settings import get_settings
    from workbench.tools.calc import EngineeringCalcTool
    from workbench.tools.registry import ToolRegistry

    settings = get_settings()
    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]

    try:
        init_engine(settings)
        registry = ModelRegistry(settings.models_manifest)
        available = await registry.probe_availability()
        if not any(available.values()):
            return SuiteResult(suite="grounding", skipped="no models are installed")

        model = registry.get_model("embed.primary")
        embedder = Embedder(registry.provider_for("embed.primary"), model)
        store = QdrantStore(settings.qdrant_url, embedder.collection)
        if await store.count() == 0:
            return SuiteResult(suite="grounding", skipped="the corpus is not indexed")

        retriever = HybridRetriever(
            embedder=embedder, vector_store=store, session_factory=get_session_factory()
        )
        residency = ResidencyManager(registry, max_resident_gb=14.0)
        router = ModelRouter(registry, config_path=settings.router_config, residency=residency)
        tools = ToolRegistry(settings.tools_config)
        tools.register(EngineeringCalcTool())
    except Exception as exc:
        return SuiteResult(suite="grounding", skipped=f"agent unavailable: {exc}")

    rbac = RbacConfig.load(settings.rbac_config)
    principal = Principal(
        user_id="eval",
        username="eval",
        roles=frozenset({"admin"}),
        permissions=rbac.permissions_for({"admin"}),
        clearance=rbac.clearance_for({"admin"}),
    )
    runner = AgentRunner(
        router=router, registry=registry, tools=tools, retriever=retriever, residency=residency
    )

    results: list[CaseResult] = []
    latencies: list[float] = []
    answerable_correct = 0
    answerable_total = 0
    refusals_correct = 0
    unanswerable_total = 0
    hallucinated_citations = 0
    broken_runs = 0
    grounded_ratios: list[float] = []

    for case in cases:
        started = time.perf_counter()
        answer = ""
        citations = 0
        unresolved = 0
        grounded = 0.0
        failure = ""
        status = ""

        async for event in runner.run(
            user_input=case["query"], principal=principal, budget=Budget(max_wall_s=240)
        ):
            if event.name == EventName.ANSWER:
                answer = str(event.data.get("text", ""))
                citations = len(event.data.get("citations") or [])
                unresolved = len(event.data.get("unresolved") or [])
            elif event.name == EventName.VALIDATION:
                grounded = float(event.data.get("grounded_ratio", 0.0))
            elif event.name == EventName.ERROR:
                # Captured rather than ignored. A run that fell over used to
                # arrive here as an empty answer, and an empty answer on an
                # unanswerable question was then reported as "answered a
                # question the corpus cannot support" — the opposite of what
                # happened, and a number that would have sent someone looking
                # for a hallucination that never occurred.
                failure = str(event.data.get("message") or event.data)[:200]
            elif event.name == EventName.RUN_FINISHED:
                status = str(event.data.get("status", ""))

        if not answer and not failure:
            failure = f"the run produced no answer (status: {status or 'unknown'})"

        latencies.append(time.perf_counter() - started)
        broken_runs += bool(failure)
        hallucinated_citations += unresolved
        grounded_ratios.append(grounded)
        # The same detector the validator uses, deliberately. Two independent
        # notions of "refused" would let the eval certify behaviour the running
        # system does not actually have.
        refused = is_refusal(answer)

        if case["answerable"]:
            answerable_total += 1
            # Answered, cited, and containing the expected figure.
            correct = (
                not refused
                and citations > 0
                and case.get("expect_phrase", "").lower() in answer.lower()
            )
            answerable_correct += correct
            results.append(
                CaseResult(
                    case_id=case["id"],
                    passed=bool(correct),
                    metrics={"citations": float(citations), "grounded": grounded},
                    detail=""
                    if correct
                    else f"expected '{case.get('expect_phrase')}' with a citation",
                    actual=answer[:160],
                )
            )
        else:
            unanswerable_total += 1
            refusals_correct += refused
            if refused:
                detail = ""
            elif failure:
                detail = failure
            else:
                detail = "answered a question the corpus cannot support"
            results.append(
                CaseResult(
                    case_id=case["id"],
                    passed=bool(refused),
                    metrics={"citations": float(citations)},
                    detail=detail,
                    actual=answer[:160],
                )
            )

    await store.aclose()
    await registry.aclose()

    return SuiteResult(
        suite="grounding",
        cases=results,
        metrics={
            # The headline anti-hallucination number.
            "refusal_rate": refusals_correct / unanswerable_total if unanswerable_total else 1.0,
            "answerable_accuracy": answerable_correct / answerable_total
            if answerable_total
            else 1.0,
            "mean_grounded_ratio": sum(grounded_ratios) / len(grounded_ratios)
            if grounded_ratios
            else 0.0,
            "hallucinated_citation_rate": hallucinated_citations / max(1, len(cases)),
            # Reported separately so a broken run can never be read as a
            # behavioural result in either direction.
            "failed_run_rate": broken_runs / max(1, len(cases)),
            "p95_s": percentile(latencies, 0.95),
            "cases": float(len(cases)),
        },
        thresholds={
            "refusal_rate": (">=", 0.75),
            "answerable_accuracy": (">=", 0.60),
            "hallucinated_citation_rate": ("<=", 0.25),
            "failed_run_rate": ("<=", 0.0),
        },
    )
