"""Model residency management.

The single most important component for running this system on a 24 GB laptop.

A reasoning model, a vision model, an embedding model and a reranker cannot all
sit in memory at once. Without admission control the machine swaps, the demo
stalls for thirty seconds mid-answer, and the "runs on-premise" claim looks
false. With it, the system behaves predictably: small models stay pinned, at
most one large model is resident, and the router *knows the price* of a swap so
it can prefer an adequate model that is already loaded over a marginally better
one that is not.

Three ideas do the work:

* **Admission control.** Before loading a model, check whether it fits under
  ``max_resident_gb``; if not, evict least-recently-used large models first.
* **A single activation lock.** Two concurrent requests that each need a
  different large model must serialise, or both load and the machine dies.
* **Measured swap cost.** Load durations are recorded per model and fed back to
  the router, so residency preference is based on observation rather than a
  guessed constant.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from workbench.core.logging import get_logger
from workbench.providers.registry import ModelRegistry
from workbench.providers.types import ModelInfo

log = get_logger(__name__)

#: Models at or below this size are cheap enough to keep loaded indefinitely.
SMALL_MODEL_GB = 4.0

#: Fallback swap-cost estimate before any real load has been timed.
DEFAULT_SWAP_COST_S = 6.0


@dataclass
class _Entry:
    logical_name: str
    size_gb: float
    pinned: bool
    last_used: float = field(default_factory=time.monotonic)
    load_count: int = 0
    total_load_s: float = 0.0

    @property
    def mean_load_s(self) -> float:
        if self.load_count == 0:
            return DEFAULT_SWAP_COST_S
        return self.total_load_s / self.load_count


class ResidencyManager:
    """Decides what may be in memory, and evicts to make room."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        max_resident_gb: float = 14.0,
        allow_swap: bool = True,
    ) -> None:
        self.registry = registry
        self.max_resident_gb = max_resident_gb
        self.allow_swap = allow_swap
        self._entries: dict[str, _Entry] = {}
        #: Serialises large-model activation. Without this, two concurrent
        #: requests can each trigger a load and blow the memory budget.
        self._activation_lock = asyncio.Lock()

    # ---------------------------------------------------------------- state
    @property
    def resident_gb(self) -> float:
        """What is actually in memory, charged once per physical model.

        Several logical names share one physical model — reasoning.primary,
        coding.primary and longctx.primary are all qwen3-8b — so summing the
        entries charged a single loaded model three times and reported 26 GB
        of a 14 GB budget for three models that were really there. That number
        drives admission control as well as the display, so the manager was
        also evicting to make room it already had.

        The largest estimate for a physical model wins: the manifest gives
        longctx.primary more RAM than reasoning.primary for the same weights
        because its context window is larger, and the KV cache is real memory.
        """
        by_physical: dict[str, float] = {}
        for logical, entry in self._entries.items():
            info = self.registry.models.get(logical)
            physical = info.physical_id if info else logical
            by_physical[physical] = max(by_physical.get(physical, 0.0), entry.size_gb)
        return sum(by_physical.values())

    def is_resident(self, logical_name: str) -> bool:
        return logical_name in self._entries

    def swap_cost_s(self, logical_name: str) -> float:
        """Estimated seconds to make this model usable right now.

        Zero when already resident — which is exactly the number the router
        needs to weigh residency against quality.
        """
        if logical_name in self._entries:
            return 0.0
        entry = self._entries.get(logical_name)
        if entry is not None:
            return entry.mean_load_s
        # No history: scale the default by size, since a 7 GB model plainly
        # takes longer to page in than a 3 GB one.
        info = self.registry.models.get(logical_name)
        if info is None:
            return DEFAULT_SWAP_COST_S
        return DEFAULT_SWAP_COST_S * max(0.5, info.approx_ram_gb / 6.0)

    def snapshot(self) -> dict[str, object]:
        """Current state, for the admin UI's residency meter.

        One row per *physical* model. Three logical names can resolve to the
        same weights — reasoning.primary, coding.primary and longctx.primary
        are all qwen3-8b — and listing them separately printed six rows whose
        sizes summed to more than the total above them, which is the kind of
        arithmetic a reader checks and then stops trusting the rest of the
        page. The aliases travel with the row instead.
        """
        grouped: dict[str, dict[str, object]] = {}
        for entry in sorted(self._entries.values(), key=lambda e: e.last_used, reverse=True):
            info = self.registry.models.get(entry.logical_name)
            physical = info.physical_id if info else entry.logical_name
            row = grouped.get(physical)
            if row is None:
                grouped[physical] = {
                    "logical_name": entry.logical_name,
                    "physical_id": physical,
                    "also_serves": [],
                    "size_gb": entry.size_gb,
                    "pinned": entry.pinned,
                    "idle_s": round(time.monotonic() - entry.last_used, 1),
                    "mean_load_s": round(entry.mean_load_s, 2),
                }
                continue
            # The same weights under another name: keep the larger estimate,
            # and stay pinned if any of the names is pinned.
            row["also_serves"] = [*row["also_serves"], entry.logical_name]  # type: ignore[misc]
            row["size_gb"] = max(float(row["size_gb"]), entry.size_gb)  # type: ignore[arg-type]
            row["pinned"] = bool(row["pinned"]) or entry.pinned

        return {
            "max_resident_gb": self.max_resident_gb,
            "resident_gb": round(self.resident_gb, 2),
            "allow_swap": self.allow_swap,
            "models": list(grouped.values()),
        }

    # ------------------------------------------------------------ activation
    async def acquire(self, logical_name: str) -> None:
        """Ensure a model is loaded and counted, evicting others if necessary.

        Small models skip the lock entirely: they are cheap, usually pinned, and
        making every embedding call queue behind a 7 GB vision load would be a
        needless serialisation of the whole pipeline.
        """
        info = self.registry.get_model(logical_name)

        if entry := self._entries.get(logical_name):
            entry.last_used = time.monotonic()
            return

        if info.approx_ram_gb <= SMALL_MODEL_GB:
            await self._load(info)
            return

        async with self._activation_lock:
            # Another request may have loaded it while we waited for the lock.
            if entry := self._entries.get(logical_name):
                entry.last_used = time.monotonic()
                return
            await self._make_room_for(info)
            await self._load(info)

    def forget(self, logical_name: str) -> None:
        """Drop the record of a model being resident.

        For when the backend says otherwise. LM Studio unloads idle models on
        its own timer, and this manager only learns of that from a periodic
        reconciliation — between the two, `acquire()` sees an entry, returns
        early, and the next request fails with "Model unloaded". Forgetting
        the entry makes the next `acquire()` load it again.
        """
        if self._entries.pop(logical_name, None) is not None:
            log.info("residency_forgotten", model=logical_name)

    async def _make_room_for(self, info: ModelInfo) -> None:
        """Evict least-recently-used models until this one fits."""
        needed = info.approx_ram_gb
        if self.resident_gb + needed <= self.max_resident_gb:
            return

        if not self.allow_swap:
            # The demo profile forbids swapping outright: a predictable stall at
            # the start beats an unpredictable one mid-sentence.
            log.warning(
                "residency_swap_forbidden",
                wanted=info.logical_name,
                resident_gb=round(self.resident_gb, 2),
                max_gb=self.max_resident_gb,
            )
            return

        evictable = sorted(
            (e for e in self._entries.values() if not e.pinned),
            key=lambda e: e.last_used,
        )
        for entry in evictable:
            if self.resident_gb + needed <= self.max_resident_gb:
                break
            await self.release(entry.logical_name)

        if self.resident_gb + needed > self.max_resident_gb:
            log.warning(
                "residency_over_budget",
                wanted=info.logical_name,
                needed_gb=needed,
                resident_gb=round(self.resident_gb, 2),
                max_gb=self.max_resident_gb,
                note="only pinned models remain; proceeding may cause swapping",
            )

    async def _load(self, info: ModelInfo) -> None:
        provider = self.registry.get_provider(info.provider)
        started = time.perf_counter()
        try:
            await provider.ensure_loaded(info.physical_id)
        except Exception as exc:
            log.warning("model_load_failed", model=info.logical_name, error=str(exc))
        elapsed = time.perf_counter() - started

        entry = self._entries.get(info.logical_name) or _Entry(
            logical_name=info.logical_name,
            size_gb=info.approx_ram_gb,
            pinned=info.pinned,
        )
        entry.last_used = time.monotonic()
        entry.load_count += 1
        entry.total_load_s += elapsed
        self._entries[info.logical_name] = entry

        log.info(
            "model_loaded",
            model=info.logical_name,
            provider=info.provider,
            load_s=round(elapsed, 2),
            resident_gb=round(self.resident_gb, 2),
        )

    async def release(self, logical_name: str) -> None:
        """Evict a model, unless it is pinned."""
        entry = self._entries.get(logical_name)
        if entry is None or entry.pinned:
            return
        info = self.registry.get_model(logical_name)
        provider = self.registry.get_provider(info.provider)
        try:
            await provider.unload(info.physical_id)
        except Exception as exc:
            log.warning("model_unload_failed", model=logical_name, error=str(exc))
        # Drop the size accounting but keep the timing history, so the next load
        # of this model is priced from real measurements.
        self._entries.pop(logical_name, None)
        entry.size_gb = 0.0
        log.info("model_evicted", model=logical_name, resident_gb=round(self.resident_gb, 2))

    # ----------------------------------------------------------------- pins
    async def warm_pinned(self, profile_name: str | None = None) -> None:
        """Load and pin the always-resident models. Called at startup.

        The small utility model backs router stage 2 and the grounding judge;
        paying its load cost inside a user's first request would be visible.
        """
        for info in self.registry.pinned_models(profile_name):
            try:
                await self._load(info)
                self._entries[info.logical_name].pinned = True
                provider = self.registry.get_provider(info.provider)
                # Ollama can hold a model open indefinitely; others cannot.
                if pin := getattr(provider, "pin", None):
                    await pin(info.physical_id)
            except Exception as exc:
                log.warning("pinned_model_warm_failed", model=info.logical_name, error=str(exc))

    async def sync_from_providers(self) -> None:
        """Reconcile our accounting with what the backends actually hold.

        The ledger drifts in both directions and both matter.

        It drifts *high* because backends evict on their own timers — LM Studio
        drops a just-in-time model an hour after it was last used — and we go
        on charging its size against the budget, so the budget slowly fills
        with models that are not there.

        It drifts *low* because a model can be loaded without us: the obvious
        case is a machine warmed before a demonstration, where somebody pins
        the chat model by hand so the first question is not a cold start. We
        would report that memory as free, and the honest number on the admin
        page is the one the operator is using to decide whether to load
        anything else.

        Correcting only the first direction, which is what this did, left the
        System page claiming a model was resident and pinned while the backend
        had long since unloaded it — and left the model that *was* loaded
        missing from the budget entirely.
        """
        # One poll per provider. This used to ask each backend again for every
        # entry it owned, which on a manifest with nine models meant nine
        # round trips to say one thing.
        reported: dict[str, set[str]] = {}
        for provider_name, provider in self.registry.providers.items():
            try:
                models = await provider.resident_models()
            except Exception as exc:
                # A backend that cannot be polled is skipped rather than
                # treated as empty: "I could not ask" and "nothing is loaded"
                # must not produce the same correction.
                log.debug("residency_poll_failed", provider=provider_name, error=str(exc))
                continue
            if models:
                reported[provider_name] = {model.physical_id for model in models}

        if not reported:
            return

        resident: set[str] = {
            logical
            for logical, info in self.registry.models.items()
            if info.physical_id in reported.get(info.provider, ())
        }

        for logical, info in self.registry.models.items():
            if info.provider not in reported:
                # This backend does not report residency; leave our own
                # accounting for it alone.
                continue
            if logical in resident and logical not in self._entries:
                log.debug("residency_adopted", model=logical, size_gb=info.approx_ram_gb)
                self._entries[logical] = _Entry(
                    logical_name=logical,
                    size_gb=info.approx_ram_gb,
                    pinned=False,
                )
            elif logical not in resident and logical in self._entries:
                log.debug("residency_drift_corrected", model=logical)
                self._entries.pop(logical, None)
