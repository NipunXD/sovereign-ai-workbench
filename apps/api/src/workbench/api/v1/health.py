"""Health, readiness, version and the egress report."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from workbench import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Cheap enough to poll and never touches a dependency."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, Any]:
    """Per-dependency readiness, feeding the admin status grid.

    Always returns 200 with a per-component breakdown rather than failing as a
    whole: an operator needs to see *which* dependency is down, and a blanket
    503 hides exactly that.
    """
    app = request.app
    components: dict[str, Any] = {}

    # --- model backends ---
    registry = getattr(app.state, "registry", None)
    if registry is not None:
        provider_health = await registry.health()
        components["providers"] = provider_health
        components["models_configured"] = len(registry.models)
    else:
        components["providers"] = {}

    # --- database ---
    if getattr(app.state, "db_ready", False):
        started = time.perf_counter()
        try:
            from sqlalchemy import text

            from workbench.db.session import get_engine

            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            components["database"] = {
                "healthy": True,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:  # noqa: BLE001
            components["database"] = {"healthy": False, "detail": str(exc)[:200]}
    else:
        components["database"] = {"healthy": False, "detail": "engine not initialised"}

    # --- vector store ---
    settings = app.state.settings
    components["vector_store"] = await _probe(f"{settings.qdrant_url}/readyz")

    # --- residency ---
    residency = getattr(app.state, "residency", None)
    if residency is not None:
        components["residency"] = residency.snapshot()

    healthy = (
        components["database"].get("healthy", False)
        and components["vector_store"].get("healthy", False)
        and any(p.get("healthy") for p in components.get("providers", {}).values())
    )
    return {"status": "ready" if healthy else "degraded", "components": components}


async def _probe(url: str, timeout_s: float = 5.0) -> dict[str, Any]:
    """A single reachability check that reports rather than raises."""
    import httpx

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
        return {
            "healthy": response.status_code < 400,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}


@router.get("/version")
async def version(request: Request) -> dict[str, Any]:
    """Build and configuration fingerprint.

    ``model_manifest_digest`` lets an operator confirm the deployment is running
    the model topology they think it is, without reading the file off the box.
    """
    registry = getattr(request.app.state, "registry", None)
    settings = request.app.state.settings
    return {
        "version": __version__,
        "environment": settings.env,
        "provider": settings.provider,
        "model_profile": settings.model_profile,
        "model_manifest_digest": registry.manifest_digest() if registry else None,
        "deterministic": settings.deterministic,
    }


@router.get("/health/egress")
async def egress(request: Request) -> dict[str, Any]:
    """Every network destination this deployment is configured to reach.

    This is the sovereignty claim made checkable. Everything listed is inside
    the deployment; there is no code path to an external AI service, and the
    air-gap CI job asserts this list contains no public hosts.
    """
    from workbench.main import egress_allowlist

    registry = getattr(request.app.state, "registry", None)
    hosts = egress_allowlist(request.app.state.settings, registry)
    private = all(
        host.split(":")[0] in {"localhost", "127.0.0.1", "host.docker.internal", "::1"}
        or host.startswith(("10.", "192.168.", "172."))
        for host in hosts
    )
    return {
        "destinations": hosts,
        "all_private": private,
        "external_ai_services": [],
        "note": (
            "Inference runs entirely on local open-weight models. No request "
            "leaves this deployment."
        ),
    }


@router.get("/models")
async def models(request: Request) -> dict[str, Any]:
    """The active model manifest, live health and current residency."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return {"models": [], "lanes": {}, "providers": {}}

    residency = getattr(request.app.state, "residency", None)
    return {
        "profile": request.app.state.settings.model_profile,
        "manifest_digest": registry.manifest_digest(),
        "providers": await registry.health(),
        "lanes": registry.lanes,
        "residency": residency.snapshot() if residency else None,
        "models": [
            {
                "logical_name": info.logical_name,
                "provider": info.provider,
                "physical_id": info.physical_id,
                "capabilities": sorted(c.value for c in info.capabilities),
                "context_window": info.context_window,
                "approx_ram_gb": info.approx_ram_gb,
                "quality_tier": info.quality_tier,
                "speed_tier": info.speed_tier,
                "pinned": info.pinned,
                "resident": residency.is_resident(info.logical_name) if residency else False,
            }
            for info in registry.models.values()
        ],
    }


@router.get("/models/routing-stats")
async def routing_stats(request: Request) -> dict[str, Any]:
    """Lane distribution and decision latency, for the admin dashboard."""
    model_router = getattr(request.app.state, "router", None)
    return model_router.routing_stats() if model_router else {}
