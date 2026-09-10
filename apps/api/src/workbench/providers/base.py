"""The provider contract.

Every model backend implements this Protocol, and every implementation must pass
``tests/unit/providers/test_contract.py``. That suite is the actual definition of
"model-agnostic": if a new backend passes it, it can ship without touching a
single call site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from workbench.providers.types import (
    GenerationChunk,
    GenerationRequest,
    GenerationResult,
    ProviderHealth,
    ResidentModel,
)


@runtime_checkable
class LLMProvider(Protocol):
    """A backend capable of running open-weight models locally.

    Implementations receive *physical* model identifiers; resolving a logical
    name to a physical one is the registry's job, not the provider's.
    """

    name: str

    async def health(self) -> ProviderHealth:
        """Report reachability. Must never raise — return unhealthy instead.

        Health is polled on a timer and shown in the admin UI; an exception here
        would take out the status page rather than reporting a red light.
        """
        ...

    async def list_models(self) -> list[str]:
        """Physical model identifiers this backend can currently serve."""
        ...

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        """Run one request to completion."""
        ...

    def stream(self, req: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        """Run one request, yielding increments as they arrive.

        Deliberately not an ``async def``: an async generator function returns
        its iterator synchronously, so callers write ``async for chunk in
        provider.stream(req)`` without an extra await.
        """
        ...

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts with the given physical embedding model."""
        ...

    async def resident_models(self) -> list[ResidentModel]:
        """Models currently held in memory.

        The residency manager uses this to decide what to evict before loading
        something large. Backends that cannot report this return an empty list,
        and the manager falls back to its own bookkeeping.
        """
        ...

    async def ensure_loaded(self, model: str) -> None:
        """Warm a model so the next request does not pay the load cost."""
        ...

    async def unload(self, model: str) -> None:
        """Evict a model to free memory. A no-op where unsupported."""
        ...

    async def aclose(self) -> None:
        """Release connections held by this provider."""
        ...


class ProviderCapabilityError(NotImplementedError):
    """Raised when a backend is asked for something it structurally cannot do.

    Distinct from a runtime failure: asking a text-only endpoint to embed is a
    programming error, not a transient outage, and must not trigger retries.
    """
