"""Deterministic provider for tests, CI and offline development.

This is not a stub that returns "hello world". It is the reason the test suite
runs on a machine with no models installed, in a CI container with no GPU, and
inside an air-gapped build with no registry access — and still exercises every
branch of the agent graph.

Three behaviours make that work:

* **Fixture lookup is content-addressed.** The key is a hash of the model,
  messages and schema, so the same request always returns the same response and
  a changed prompt visibly misses rather than silently reusing stale output.
* **Unknown keys synthesise a schema-valid response** instead of raising, so
  adding a test does not require hand-authoring a fixture before it can run.
* **Misses are recorded** to ``mock_misses.jsonl``, which is how fixtures get
  authored: run the test, look at what was asked, paste in a real answer.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import yaml

from workbench.core.hashing import canonical_json, digest
from workbench.core.logging import get_logger
from workbench.providers.types import (
    GenerationChunk,
    GenerationRequest,
    GenerationResult,
    ProviderHealth,
    ResidentModel,
    ToolCall,
    Usage,
)

log = get_logger(__name__)

#: Simulated per-token delay. Zero by default so tests are fast; raise it to
#: watch the UI behave under realistic streaming.
DEFAULT_TOKEN_DELAY_S = 0.0


class MockProvider:
    """Replays recorded responses; synthesises plausible ones when it must."""

    name = "mock"

    def __init__(
        self,
        *,
        fixtures_path: Path | None = None,
        token_delay_s: float = DEFAULT_TOKEN_DELAY_S,
        record_misses: bool = True,
    ) -> None:
        self.fixtures_path = fixtures_path
        self.token_delay_s = token_delay_s
        self.record_misses = record_misses
        self._fixtures: dict[str, Any] = {}
        self._resident: dict[str, float] = {}
        #: Every request seen, so tests can assert on routing decisions.
        self.calls: list[GenerationRequest] = []
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        if not self.fixtures_path or not self.fixtures_path.is_file():
            return
        try:
            loaded = yaml.safe_load(self.fixtures_path.read_text(encoding="utf-8"))
            self._fixtures = loaded.get("responses", {}) if loaded else {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("mock_fixtures_unreadable", path=str(self.fixtures_path), error=str(exc))

    # ------------------------------------------------------------------ keys
    @staticmethod
    def fixture_key(req: GenerationRequest) -> str:
        """Content-address a request.

        Images contribute their digest rather than their bytes so a fixture file
        stays readable and a 5 MB scan does not end up in a test key.
        """
        payload = {
            "model": req.model,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "images": [digest(image.data) for image in m.images],
                }
                for m in req.messages
            ],
            "json_schema": req.json_schema,
            "tools": sorted(t.name for t in req.tools) if req.tools else None,
        }
        return digest(payload)

    def _lookup(self, req: GenerationRequest) -> dict[str, Any] | None:
        if entry := self._fixtures.get(self.fixture_key(req)):
            return entry if isinstance(entry, dict) else {"text": str(entry)}

        # Fall back to a named fixture, which is far easier to write by hand
        # than a hash when a test wants to pin one specific interaction.
        if name := req.metadata.get("fixture"):
            entry = self._fixtures.get(str(name))
            if entry is not None:
                return entry if isinstance(entry, dict) else {"text": str(entry)}
        return None

    def _record_miss(self, req: GenerationRequest) -> None:
        if not self.record_misses or not self.fixtures_path:
            return
        try:
            path = self.fixtures_path.with_name("mock_misses.jsonl")
            record = {
                "key": self.fixture_key(req),
                "model": req.model,
                "messages": [{"role": m.role, "content": m.content[:2000]} for m in req.messages],
                "json_schema": req.json_schema,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ synthesis
    @staticmethod
    def _synthesise_from_schema(schema: dict[str, Any]) -> Any:
        """Build a minimal value satisfying a JSON schema.

        Structured-output call sites parse and validate what comes back, so a
        response that does not fit the schema would fail somewhere confusing.
        Producing a valid shape keeps the failure where it belongs — in the
        assertion about content, not in a parse error.
        """
        schema_type = schema.get("type")
        if enum := schema.get("enum"):
            return enum[0]
        if schema_type == "object":
            properties = schema.get("properties") or {}
            required = schema.get("required") or list(properties)
            return {
                key: MockProvider._synthesise_from_schema(properties.get(key, {}))
                for key in required
            }
        if schema_type == "array":
            items = schema.get("items") or {}
            return [MockProvider._synthesise_from_schema(items)] if items else []
        if schema_type == "integer":
            return 0
        if schema_type == "number":
            return 0.0
        if schema_type == "boolean":
            return False
        if schema_type == "null":
            return None
        return "mock"

    def _synthesise(self, req: GenerationRequest) -> str:
        if req.json_schema is not None:
            return canonical_json(self._synthesise_from_schema(req.json_schema))

        last_user = next(
            (m.content for m in reversed(req.messages) if m.role == "user"),
            "",
        )
        # Echo a trimmed form of the question. Deterministic, obviously fake, and
        # long enough that streaming and markdown rendering get exercised.
        subject = re.sub(r"\s+", " ", last_user).strip()[:160]
        return (
            f"[mock:{req.model}] This is a deterministic stand-in response. "
            f"The request was: {subject}"
        )

    # -------------------------------------------------------------- protocol
    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            detail="deterministic fixture provider",
            models_available=len(self._fixtures),
        )

    async def list_models(self) -> list[str]:
        return ["mock-chat", "mock-vision", "mock-embed"]

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        self.calls.append(req)
        fixture = self._lookup(req)
        if fixture is None:
            self._record_miss(req)
            text, tool_calls = self._synthesise(req), []
        else:
            text = str(fixture.get("text", ""))
            tool_calls = [
                ToolCall(
                    id=call.get("id", f"call_{index}"),
                    name=call["name"],
                    arguments=call.get("arguments", {}),
                )
                for index, call in enumerate(fixture.get("tool_calls") or [])
            ]

        return GenerationResult(
            text=text,
            model_used=req.model,
            finish_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls,
            usage=Usage(
                prompt_tokens=req.estimated_prompt_tokens(),
                completion_tokens=max(1, len(text) // 4),
            ),
            latency_ms=1,
            ttft_ms=1,
        )

    async def stream(self, req: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        result = await self.generate(req)
        # Chunk on whitespace so the frontend sees realistic token boundaries.
        for token in re.findall(r"\S+\s*", result.text):
            if self.token_delay_s:
                await asyncio.sleep(self.token_delay_s)
            yield GenerationChunk(delta=token)
        yield GenerationChunk(
            done=True,
            tool_calls=result.tool_calls,
            meta={
                "model_used": result.model_used,
                "finish_reason": result.finish_reason,
                "latency_ms": result.latency_ms,
                "ttft_ms": result.ttft_ms,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
            },
        )

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Hash-derived unit vectors.

        Deterministic and, critically, *similar inputs do not produce similar
        vectors*. That keeps retrieval tests honest: anything that passes here
        passes because of the retrieval logic, not because the fake embeddings
        happened to be semantically arranged.
        """
        import hashlib
        import struct

        dimensions = 768
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(f"{model}:{text}".encode()).digest()
            # Stretch the digest to the required width deterministically.
            raw = b"".join(
                hashlib.sha256(seed + index.to_bytes(4, "big")).digest()
                for index in range((dimensions * 4 // 32) + 1)
            )
            values = [
                struct.unpack_from("<i", raw, offset * 4)[0] / 2**31
                for offset in range(dimensions)
            ]
            norm = sum(value * value for value in values) ** 0.5 or 1.0
            vectors.append([value / norm for value in values])
        return vectors

    async def resident_models(self) -> list[ResidentModel]:
        return [ResidentModel(physical_id=name, size_gb=size) for name, size in self._resident.items()]

    async def ensure_loaded(self, model: str) -> None:
        self._resident[model] = 1.0

    async def unload(self, model: str) -> None:
        self._resident.pop(model, None)

    async def aclose(self) -> None:
        return None

    # ---------------------------------------------------------- test helpers
    def set_response(self, req: GenerationRequest, text: str, **extra: Any) -> None:
        """Pin a response for an exact request. For use inside a single test."""
        self._fixtures[self.fixture_key(req)] = {"text": text, **extra}

    def set_named_response(self, name: str, text: str, **extra: Any) -> None:
        """Pin a response addressed by ``metadata={"fixture": name}``."""
        self._fixtures[name] = {"text": text, **extra}

    def reset(self) -> None:
        self.calls.clear()
        self._resident.clear()
