"""FastAPI application factory.

Startup wires the object graph once — registry, residency manager, router,
database, event bus — and hands them to request handlers through
``app.state``. Nothing constructs a provider or reads the manifest on a request
path.

Sovereignty note: every outbound connection this process makes goes to a host
named in ``config/models.yaml`` or the database/vector-store/queue URLs. There
is no code path to a third-party AI service, and ``GET /api/v1/health/egress``
reports the exact set of destinations so the claim can be checked rather than
taken on faith.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from workbench import __version__
from workbench.core.clock import FrozenClock, set_clock
from workbench.core.errors import AppError
from workbench.core.events import get_event_bus
from workbench.core.ids import new_id, set_deterministic
from workbench.core.logging import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
)
from workbench.providers.registry import ModelRegistry
from workbench.providers.residency import ResidencyManager
from workbench.router.router import ModelRouter
from workbench.security.auth import TokenService
from workbench.security.rbac import RbacConfig
from workbench.settings import Settings, get_settings

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the object graph on startup, tear it down on shutdown."""
    settings: Settings = app.state.settings

    configure_logging(settings.log_level, json_output=settings.is_production)
    if settings.deterministic:
        set_clock(FrozenClock())
        set_deterministic(True)
        log.warning("deterministic_mode_enabled")

    settings.ensure_directories()

    # --- access control ---
    app.state.rbac = RbacConfig.load(settings.rbac_config)
    app.state.tokens = TokenService(
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        access_minutes=settings.access_token_minutes,
    )

    # --- models ---
    registry = ModelRegistry(settings.models_manifest)
    residency = ResidencyManager(
        registry,
        max_resident_gb=float(
            registry.profile(settings.model_profile).get("max_resident_gb", 14.0)
        ),
        allow_swap=bool(registry.profile(settings.model_profile).get("allow_swap", True)),
    )
    app.state.registry = registry
    app.state.residency = residency
    app.state.router = ModelRouter(
        registry,
        config_path=settings.router_config,
        residency=residency,
        profile=settings.model_profile,
    )
    app.state.events = get_event_bus()

    # --- sandbox and artifacts ---
    from workbench.artifacts.store import ArtifactStore
    from workbench.sandbox.fake_backend import build_backend

    app.state.artifact_store = ArtifactStore(settings.artifact_dir)
    sandbox = build_backend(
        settings.sandbox_backend,
        workspace_root=settings.workspace_dir,
        image=settings.sandbox_image,
    )
    app.state.sandbox = sandbox
    healthy, detail = await sandbox.health()
    log.info(
        "sandbox_ready" if healthy else "sandbox_unavailable", backend=sandbox.name, detail=detail
    )
    # Containers left by a crashed process hold their memory reservation, so
    # they are cleared before this one starts creating more.
    if reap := getattr(sandbox, "reap_orphans", None):
        await reap()

    # --- tools ---
    # Registered here rather than discovered by import side effect, so the set
    # of things the agent can do is visible in one place.
    from workbench.tools.artifacts import DocxTool, PptxTool, XlsxTool
    from workbench.tools.calc import EngineeringCalcTool
    from workbench.tools.code import RunPythonTool
    from workbench.tools.registry import ToolRegistry

    # Files produced by sandbox runs, so a later artifact step can embed a chart
    # an earlier step generated.
    app.state.generated_files = {}

    tools = ToolRegistry(settings.tools_config)
    tools.register_all(
        EngineeringCalcTool(),
        RunPythonTool(sandbox, artifact_files=app.state.generated_files),
        DocxTool(app.state.artifact_store, images=app.state.generated_files),
        XlsxTool(app.state.artifact_store),
        PptxTool(app.state.artifact_store, images=app.state.generated_files),
    )
    app.state.tools = tools

    # --- database ---
    try:
        from workbench.db.session import dispose_engine, init_engine

        init_engine(settings)
        app.state.db_ready = True

        # The approval gate needs durable state: the approver is a different
        # person, at a different browser, often after a restart.
        from workbench.agent.approval import ApprovalGate
        from workbench.db.session import get_session_factory

        app.state.approval_gate = ApprovalGate(get_session_factory())
    except Exception as exc:
        # The service still serves /health and the admin status grid without a
        # database, which is what an operator needs in order to diagnose why.
        log.error("database_init_failed", error=str(exc))
        app.state.db_ready = False
        app.state.approval_gate = None

    # A manifest declares intent; what is installed is a separate fact. Probing
    # at boot keeps the router from selecting a model that was never pulled and
    # only discovering it mid-request.
    try:
        availability = await registry.probe_availability()
        missing = sorted(name for name, ok in availability.items() if not ok)
        if missing:
            log.warning("models_missing", models=missing, hint="scripts/pull_models.sh")
    except Exception as exc:
        log.warning("model_availability_probe_failed", error=str(exc))

    # --- retrieval ---
    # Optional: the service still serves chat and health without a vector store,
    # which is what an operator needs in order to diagnose one that is down.
    try:
        from workbench.rag.embedder import Embedder
        from workbench.rag.retriever import HybridRetriever
        from workbench.rag.vectorstore.qdrant_store import QdrantStore

        embed_model = registry.get_model("embed.primary")
        embedder = Embedder(registry.provider_for("embed.primary"), embed_model)
        store = QdrantStore(settings.qdrant_url, embedder.collection)
        await store.ensure_collection(embedder.dimensions)

        session_factory = None
        if app.state.db_ready:
            from workbench.db.session import get_session_factory

            session_factory = get_session_factory()

        app.state.retriever = HybridRetriever(
            embedder=embedder, vector_store=store, session_factory=session_factory
        )
        app.state.vector_store = store

        # --- ingestion ---
        from workbench.ingest.indexer import DocumentIndexer
        from workbench.ingest.ocr.engine import build_engine
        from workbench.ingest.pipeline import IngestionPipeline, PipelineConfig
        from workbench.ingest.storage import BlobStore
        from workbench.ingest.vision.reader import VisionReader
        from workbench.rag.chunker import Chunker, ChunkSpec

        app.state.pipeline = IngestionPipeline(
            blob_store=BlobStore(settings.blob_dir),
            page_image_dir=settings.page_image_dir,
            dataset_dir=settings.data_dir / "datasets",
            # The embedding model's context window is the hard ceiling on a
            # chunk; overrunning it is truncated silently by the backend.
            chunker=Chunker(ChunkSpec(max_child_tokens=int(embed_model.context_window * 0.9))),
            ocr_engine=build_engine("rapidocr"),
            vision_reader=VisionReader(registry=registry, residency=residency),
            config=PipelineConfig(max_upload_mb=settings.max_upload_mb),
        )
        app.state.indexer = DocumentIndexer(embedder=embedder, vector_store=store)

        log.info("retrieval_ready", collection=embedder.collection, dimensions=embedder.dimensions)
    except Exception as exc:
        app.state.retriever = None
        app.state.vector_store = None
        app.state.pipeline = None
        app.state.indexer = None
        log.warning("retrieval_unavailable", error=str(exc))

    # A run only ends when its own request ends, so a process that exits mid-run
    # leaves rows marked running with nothing left alive to close them. Anything
    # still running from a previous process is, by definition, not.
    from workbench.api.v1.runs import reap_orphans

    await reap_orphans()

    log.info(
        "workbench_started",
        version=__version__,
        env=settings.env,
        provider=settings.provider,
        profile=settings.model_profile,
        models=len(registry.models),
    )

    # Warming the pinned models costs a few seconds now and removes that cost
    # from the first user request, where it would be plainly visible.
    try:
        await residency.warm_pinned(settings.model_profile)
    except Exception as exc:
        log.warning("pinned_warm_failed", error=str(exc))

    yield

    await registry.aclose()
    if getattr(app.state, "vector_store", None) is not None:
        await app.state.vector_store.aclose()
    if app.state.db_ready:
        from workbench.db.session import dispose_engine

        await dispose_engine()
    log.info("workbench_stopped")


class RequestContextMiddleware:
    """Attach a correlation id to every request, as raw ASGI middleware.

    Deliberately not written with ``@app.middleware("http")``. That builds a
    Starlette ``BaseHTTPMiddleware``, which pumps the response through an anyio
    memory stream — and that wrapper cancels long-lived streaming responses part
    way through. The symptom is a chat run that dies silently after a minute,
    mid-trace, with a CancelledError buried in the connection pool. Since every
    interesting endpoint here streams, the middleware has to leave the response
    alone.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        request_id = headers.get("x-request-id") or f"req_{new_id()}"
        started = time.perf_counter()
        bind_request_context(
            request_id=request_id,
            path=scope.get("path", ""),
            method=scope.get("method", ""),
        )

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                elapsed = (time.perf_counter() - started) * 1000
                message.setdefault("headers", [])
                message["headers"] = [
                    *message["headers"],
                    (b"x-request-id", request_id.encode("latin-1")),
                    (b"x-response-time-ms", f"{elapsed:.1f}".encode("latin-1")),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_request_context()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Sovereign On-Premise Agentic AI Workbench — SIH 2026 PS 26117 (MRPL). "
            "All inference runs on local open-weight models; no external AI service "
            "is contacted."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """Render deliberate failures as RFC 7807 problem documents."""
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_problem(instance=str(request.url.path)),
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Never leak an internal error message or stack trace to a client."""
        log.exception("unhandled_error", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "type": "https://workbench.local/errors/internal_error",
                "title": "InternalServerError",
                "status": 500,
                "detail": "An unexpected error occurred.",
                "code": "internal_error",
                "instance": str(request.url.path),
            },
            media_type="application/problem+json",
        )

    from workbench.api.v1 import (
        approvals,
        audit,
        auth,
        chat,
        documents,
        health,
        search,
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")

    return app


def egress_allowlist(settings: Settings, registry: ModelRegistry | None = None) -> list[str]:
    """Every host this process is configured to contact.

    Backs the sovereignty claim with something checkable: if a destination is
    not on this list, no code path reaches it. Rendered in the admin UI and
    asserted by the air-gap test in CI.
    """
    urls = [settings.database_url, settings.qdrant_url, settings.redis_url]
    if registry is not None:
        for provider in registry.providers.values():
            if base := getattr(provider, "base_url", None):
                urls.append(str(base))

    hosts: set[str] = set()
    for url in urls:
        parsed = urlparse(url if "://" in url else f"//{url}")
        if parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            hosts.add(f"{parsed.hostname}{port}")
    return sorted(hosts)


app = create_app()
