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
        max_resident_gb=float(registry.profile(settings.model_profile).get("max_resident_gb", 14.0)),
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

    # --- tools ---
    # Registered here rather than discovered by import side effect, so the set
    # of things the agent can do is visible in one place.
    from workbench.tools.calc import EngineeringCalcTool
    from workbench.tools.registry import ToolRegistry

    tools = ToolRegistry(settings.tools_config)
    tools.register_all(EngineeringCalcTool())
    app.state.tools = tools

    # --- database ---
    try:
        from workbench.db.session import dispose_engine, init_engine

        init_engine(settings)
        app.state.db_ready = True
    except Exception as exc:  # noqa: BLE001
        # The service still serves /health and the admin status grid without a
        # database, which is what an operator needs in order to diagnose why.
        log.error("database_init_failed", error=str(exc))
        app.state.db_ready = False

    # A manifest declares intent; what is installed is a separate fact. Probing
    # at boot keeps the router from selecting a model that was never pulled and
    # only discovering it mid-request.
    try:
        availability = await registry.probe_availability()
        missing = sorted(name for name, ok in availability.items() if not ok)
        if missing:
            log.warning("models_missing", models=missing, hint="scripts/pull_models.sh")
    except Exception as exc:  # noqa: BLE001
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
        log.info("retrieval_ready", collection=embedder.collection, dimensions=embedder.dimensions)
    except Exception as exc:  # noqa: BLE001
        app.state.retriever = None
        app.state.vector_store = None
        log.warning("retrieval_unavailable", error=str(exc))

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
    except Exception as exc:  # noqa: BLE001
        log.warning("pinned_warm_failed", error=str(exc))

    yield

    await registry.aclose()
    if getattr(app.state, "vector_store", None) is not None:
        await app.state.vector_store.aclose()
    if app.state.db_ready:
        from workbench.db.session import dispose_engine

        await dispose_engine()
    log.info("workbench_stopped")


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

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        """Tag every log line and response with a correlation id."""
        request_id = request.headers.get("x-request-id") or f"req_{new_id()}"
        started = time.perf_counter()
        bind_request_context(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response

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

    from workbench.api.v1 import auth, chat, health

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")

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
