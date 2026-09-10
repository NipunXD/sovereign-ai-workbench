"""Model registry — resolves logical names to running models.

This is where "a new model can be added without redesign" stops being a claim
and becomes a mechanism. Call sites ask for ``reasoning.primary``; this class
reads ``config/models.yaml``, picks a provider, and hands back something that
satisfies the ``LLMProvider`` protocol. Swapping the laptop for an air-gapped
GPU server is an edit to that YAML file.

The registry is also the only place that knows a provider *exists*. Everything
above it — the router, the agent, every tool — deals in logical names and lanes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from workbench.core.errors import ConfigurationError
from workbench.core.logging import get_logger
from workbench.providers.base import LLMProvider
from workbench.providers.mock import MockProvider
from workbench.providers.ollama import OllamaProvider
from workbench.providers.openai_compat import OpenAICompatProvider
from workbench.providers.types import Capability, Lane, ModelInfo

log = get_logger(__name__)

#: ${VAR} and ${VAR:-default} inside manifest strings.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Substitute environment variables throughout a loaded config tree.

    Endpoints differ between a laptop, CI and the deployed rack, and they are
    the only thing that differs — so they come from the environment while the
    model topology stays in version control.
    """
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else ""),
            value,
        )
    if isinstance(value, dict):
        return {key: _expand_env(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


class ModelRegistry:
    """Loads the manifest and owns one provider instance per configured backend."""

    def __init__(self, manifest_path: Path, *, fixtures_path: Path | None = None) -> None:
        self.manifest_path = manifest_path
        self._fixtures_path = fixtures_path
        self._providers: dict[str, LLMProvider] = {}
        self._models: dict[str, ModelInfo] = {}
        self._lanes: dict[str, list[str]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._rerank: dict[str, Any] = {}
        self._raw: dict[str, Any] = {}
        #: Logical names confirmed present on their backend. Empty until
        #: ``probe_availability`` runs; see ``lane_candidates``.
        self._available: set[str] | None = None
        self.load()

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        """Read the manifest and (re)build providers. Safe to call at runtime."""
        if not self.manifest_path.is_file():
            raise ConfigurationError(f"model manifest not found: {self.manifest_path}")
        try:
            raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{self.manifest_path} is not valid YAML: {exc}") from exc

        self._raw = _expand_env(raw)
        self._build_providers(self._raw.get("providers") or {})
        self._build_models(self._raw.get("models") or [])
        self._lanes = {str(k): list(v) for k, v in (self._raw.get("lanes") or {}).items()}
        self._profiles = dict(self._raw.get("profiles") or {})
        self._rerank = dict(self._raw.get("rerank") or {})
        self._validate()

        log.info(
            "model_manifest_loaded",
            providers=sorted(self._providers),
            models=len(self._models),
            lanes=sorted(self._lanes),
        )

    def _build_providers(self, config: dict[str, Any]) -> None:
        providers: dict[str, LLMProvider] = {}
        for name, spec in config.items():
            if not spec.get("enabled", True):
                continue
            kind = spec.get("type", name)
            timeout = float(spec.get("timeout_s", 300))
            try:
                if kind == "ollama":
                    providers[name] = OllamaProvider(
                        base_url=spec.get("base_url", "http://localhost:11434"),
                        timeout_s=timeout,
                    )
                elif kind in {"openai_compatible", "vllm", "lmstudio"}:
                    providers[name] = OpenAICompatProvider(
                        name=name,
                        base_url=spec["base_url"],
                        timeout_s=timeout,
                        admin_url=spec.get("native_admin_url"),
                        # vLLM's structured output extension differs from OpenAI's.
                        structured_output_mode=(
                            "guided_json" if name == "vllm" else "json_schema"
                        ),
                    )
                elif kind == "mock":
                    fixtures = spec.get("fixtures")
                    providers[name] = MockProvider(
                        fixtures_path=(
                            self._fixtures_path
                            or (self.manifest_path.parents[1] / fixtures if fixtures else None)
                        )
                    )
                else:
                    raise ConfigurationError(f"unknown provider type '{kind}' for '{name}'")
            except KeyError as exc:
                raise ConfigurationError(f"provider '{name}' is missing {exc}") from exc
        self._providers = providers

    def _build_models(self, entries: list[dict[str, Any]]) -> None:
        models: dict[str, ModelInfo] = {}
        for entry in entries:
            try:
                logical = entry["logical"]
                capabilities = frozenset(
                    Capability(c) for c in entry.get("capabilities", ["chat"])
                )
                models[logical] = ModelInfo(
                    logical_name=logical,
                    provider=entry["provider"],
                    physical_id=entry["physical"],
                    capabilities=capabilities,
                    context_window=int(entry.get("context_window", 8192)),
                    approx_ram_gb=float(entry.get("approx_ram_gb", 0.0)),
                    quality_tier=int(entry.get("quality_tier", 2)),
                    speed_tier=int(entry.get("speed_tier", 2)),
                    default_num_ctx=entry.get("default_num_ctx"),
                    dimensions=entry.get("dimensions"),
                    pinned=bool(entry.get("pinned", False)),
                    optional=bool(entry.get("optional", False)),
                )
            except KeyError as exc:
                raise ConfigurationError(f"model entry missing required key {exc}") from exc
            except ValueError as exc:
                raise ConfigurationError(f"model '{entry.get('logical')}': {exc}") from exc
        self._models = models

    def _validate(self) -> None:
        """Fail loudly at startup rather than mysteriously at request time."""
        problems: list[str] = []

        for logical, info in self._models.items():
            if info.provider not in self._providers:
                # Not fatal on its own: a manifest may describe the GPU server's
                # vLLM models while running on a laptop where vLLM is disabled.
                log.debug(
                    "model_provider_disabled", logical=logical, provider=info.provider
                )

        for lane, candidates in self._lanes.items():
            try:
                Lane(lane)
            except ValueError:
                problems.append(f"lane '{lane}' is not a recognised lane")
            for candidate in candidates:
                if candidate not in self._models:
                    problems.append(f"lane '{lane}' references unknown model '{candidate}'")

        for name, profile in self._profiles.items():
            for pinned in profile.get("pin") or []:
                if pinned not in self._models:
                    problems.append(f"profile '{name}' pins unknown model '{pinned}'")

        if Lane.VISION.value in self._lanes:
            vision_candidates = self._lanes[Lane.VISION.value]
            for candidate in vision_candidates:
                info = self._models.get(candidate)
                if info and not info.supports(Capability.VISION):
                    # A text model in the vision lane would answer confidently
                    # about an image it never received. Refuse to start.
                    problems.append(
                        f"vision lane candidate '{candidate}' lacks the vision capability"
                    )

        if problems:
            raise ConfigurationError(
                "model manifest is invalid:\n  - " + "\n  - ".join(problems)
            )

    # ------------------------------------------------------------- accessors
    def get_model(self, logical_name: str) -> ModelInfo:
        try:
            return self._models[logical_name]
        except KeyError:
            raise ConfigurationError(
                f"no model '{logical_name}' in {self.manifest_path.name}"
            ) from None

    def get_provider(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise ConfigurationError(
                f"provider '{name}' is not configured or not enabled"
            ) from None

    def provider_for(self, logical_name: str) -> LLMProvider:
        """The backend that serves this logical model."""
        return self.get_provider(self.get_model(logical_name).provider)

    def lane_candidates(self, lane: Lane | str) -> list[ModelInfo]:
        """Ordered candidates for a lane.

        Excludes disabled backends and — once availability has been probed —
        models the backend does not actually serve. A manifest describes intent;
        what is installed is a separate fact, and routing to a model that was
        never pulled would fail at request time and only then fall back.
        """
        key = lane.value if isinstance(lane, Lane) else lane
        candidates = [
            self._models[name]
            for name in self._lanes.get(key, [])
            if name in self._models and self._models[name].provider in self._providers
        ]
        if self._available is None:
            return candidates

        present = [info for info in candidates if info.logical_name in self._available]
        # If nothing in the lane is installed, hand back the declared candidates
        # anyway: a clear downstream failure naming the missing model beats an
        # opaque "no model satisfies this request".
        return present or candidates

    async def probe_availability(self) -> dict[str, bool]:
        """Ask each backend which manifest models it can actually serve.

        Called at startup and by ``POST /models/reload``. Ollama reports tags
        with an implicit ``:latest``, so comparison normalises that away.
        """
        available: set[str] = set()
        report: dict[str, bool] = {}
        by_provider: dict[str, set[str]] = {}

        for name, provider in self._providers.items():
            try:
                served = await provider.list_models()
            except Exception as exc:  # noqa: BLE001 - a down backend is not fatal
                log.warning("provider_model_list_failed", provider=name, error=str(exc))
                by_provider[name] = set()
                continue
            normalised: set[str] = set()
            for model_id in served:
                normalised.add(model_id)
                if model_id.endswith(":latest"):
                    normalised.add(model_id[: -len(":latest")])
            by_provider[name] = normalised

        for logical, info in self._models.items():
            if info.provider == "mock":
                available.add(logical)
                report[logical] = True
                continue
            served = by_provider.get(info.provider)
            if served is None:
                report[logical] = False
                continue
            present = info.physical_id in served or f"{info.physical_id}:latest" in served
            report[logical] = present
            if present:
                available.add(logical)

        self._available = available
        missing = sorted(name for name, ok in report.items() if not ok)
        if missing:
            log.warning(
                "models_declared_but_not_installed",
                missing=missing,
                hint="run scripts/pull_models.sh",
            )
        return report

    def is_available(self, logical_name: str) -> bool:
        """Whether the backend confirmed it serves this model."""
        if self._available is None:
            return True
        return logical_name in self._available

    def profile(self, name: str) -> dict[str, Any]:
        if name not in self._profiles:
            raise ConfigurationError(f"no profile '{name}' in {self.manifest_path.name}")
        return self._profiles[name]

    @property
    def rerank_config(self) -> dict[str, Any]:
        return self._rerank

    @property
    def models(self) -> dict[str, ModelInfo]:
        return dict(self._models)

    @property
    def providers(self) -> dict[str, LLMProvider]:
        return dict(self._providers)

    @property
    def lanes(self) -> dict[str, list[str]]:
        return dict(self._lanes)

    @property
    def default_profile(self) -> str:
        return str(self._raw.get("default_profile", "balanced"))

    def pinned_models(self, profile_name: str | None = None) -> list[ModelInfo]:
        """Models that should stay resident: manifest-level plus profile-level."""
        pinned = {info.logical_name for info in self._models.values() if info.pinned}
        if profile_name:
            pinned.update(self._profiles.get(profile_name, {}).get("pin") or [])
        return [self._models[name] for name in sorted(pinned) if name in self._models]

    def manifest_digest(self) -> str:
        """Fingerprint of the active manifest, surfaced by ``GET /version``.

        Lets an operator confirm that the rack is running the model topology
        they think it is.
        """
        from workbench.core.hashing import digest

        return digest(self._raw)

    async def health(self) -> dict[str, Any]:
        """Poll every configured backend."""
        results = {}
        for name, provider in self._providers.items():
            health = await provider.health()
            results[name] = {
                "healthy": health.healthy,
                "detail": health.detail,
                "latency_ms": health.latency_ms,
                "models_available": health.models_available,
            }
        return results

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
