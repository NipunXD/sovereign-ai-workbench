"""Ollama provider.

Ollama is not OpenAI-compatible in the ways that matter to us, so this talks its
native API rather than its compatibility shim:

* ``/api/chat`` streams newline-delimited JSON, not SSE.
* Images go in an ``images`` array of bare base64 strings, not data URLs.
* ``/api/ps`` reports exactly which models are resident and how much memory they
  hold — the single most useful signal the residency manager has, and something
  the OpenAI protocol has no equivalent for.
* ``keep_alive`` controls idle eviction directly, which is how small models get
  pinned.

In this deployment Ollama serves the vision lane while LM Studio serves the text
lanes, so both implementations are exercised on every multimodal request.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from workbench.core.ids import new_id
from workbench.core.logging import get_logger
from workbench.providers.errors import (
    ContextLengthExceededError,
    ModelNotFoundError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from workbench.providers.types import (
    ChatMessage,
    GenerationChunk,
    GenerationRequest,
    GenerationResult,
    ProviderHealth,
    ResidentModel,
    ToolCall,
    Usage,
)

log = get_logger(__name__)


class OllamaProvider:
    """Talks Ollama's native HTTP API."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    # ------------------------------------------------------------------ health
    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = await self._client.get("/api/tags", timeout=10.0)
            response.raise_for_status()
            models = response.json().get("models", [])
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                models_available=len(models),
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"ollama is not reachable at {self.base_url}: {exc}"
            ) from exc
        return [entry["name"] for entry in response.json().get("models", [])]

    # ----------------------------------------------------------- payload build
    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.images:
            # Ollama wants bare base64 — no "data:" prefix, unlike OpenAI.
            payload["images"] = [image.to_base64() for image in message.images]
        return payload

    def _build_payload(self, req: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": req.temperature}
        if req.num_ctx is not None:
            options["num_ctx"] = req.num_ctx
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.seed is not None:
            options["seed"] = req.seed
        if req.stop:
            options["stop"] = req.stop

        payload: dict[str, Any] = {
            "model": req.model,
            "messages": [self._message_payload(m) for m in req.messages],
            "stream": stream,
            "options": options,
        }
        if req.json_schema is not None:
            # Ollama constrains generation to a raw JSON schema in `format`.
            payload["format"] = req.json_schema
        if req.tools:
            payload["tools"] = [tool.to_openai() for tool in req.tools]
        if req.keep_alive is not None:
            payload["keep_alive"] = req.keep_alive
        return payload

    # --------------------------------------------------------------- generate
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        payload = self._build_payload(req, stream=False)
        try:
            response = await self._client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"ollama timed out on {req.model}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc

        self._raise_for_status(response, req.model)

        try:
            body = response.json()
            message = body.get("message") or {}
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(f"ollama returned unparseable JSON: {exc}") from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return GenerationResult(
            text=message.get("content") or "",
            # Ollama names the thinking-model monologue "thinking".
            reasoning=message.get("thinking") or "",
            model_used=body.get("model", req.model),
            finish_reason=body.get("done_reason") or "stop",
            tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            usage=Usage(
                prompt_tokens=int(body.get("prompt_eval_count", 0)),
                completion_tokens=int(body.get("eval_count", 0)),
            ),
            latency_ms=elapsed_ms,
            ttft_ms=elapsed_ms,
        )

    async def stream(self, req: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        started = time.perf_counter()
        ttft_ms = 0
        thinking_ms = 0
        tool_calls: list[ToolCall] = []
        usage = Usage()
        finish_reason = "stop"
        model_used = req.model
        payload = self._build_payload(req, stream=True)

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, req.model)

                # Ollama streams newline-delimited JSON objects, not SSE frames.
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("ollama_chunk_unparseable", model=req.model)
                        continue

                    model_used = event.get("model", model_used)
                    message = event.get("message") or {}

                    if calls := message.get("tool_calls"):
                        tool_calls.extend(self._parse_tool_calls(calls))

                    if reasoning := message.get("thinking"):
                        yield GenerationChunk(reasoning_delta=reasoning)

                    if text := message.get("content"):
                        if ttft_ms == 0:
                            ttft_ms = int((time.perf_counter() - started) * 1000)
                            thinking_ms = ttft_ms
                        yield GenerationChunk(delta=text)

                    if event.get("done"):
                        finish_reason = event.get("done_reason") or "stop"
                        usage = Usage(
                            prompt_tokens=int(event.get("prompt_eval_count", 0)),
                            completion_tokens=int(event.get("eval_count", 0)),
                        )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"ollama timed out on {req.model}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc

        yield GenerationChunk(
            done=True,
            tool_calls=tool_calls,
            meta={
                "model_used": model_used,
                "finish_reason": finish_reason,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "ttft_ms": ttft_ms,
                "thinking_ms": thinking_ms,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
        )

    # ------------------------------------------------------------------ embed
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post("/api/embed", json={"model": model, "input": texts})
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("ollama timed out embedding") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"ollama unreachable: {exc}") from exc

        self._raise_for_status(response, model)
        try:
            return list(response.json()["embeddings"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError("ollama returned a malformed embedding response") from exc

    # -------------------------------------------------------------- residency
    async def resident_models(self) -> list[ResidentModel]:
        """Models currently loaded, straight from ``/api/ps``.

        This is the ground truth the residency manager needs: without it, memory
        admission control would be guesswork based on our own bookkeeping.
        """
        try:
            response = await self._client.get("/api/ps", timeout=5.0)
            response.raise_for_status()
            entries = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return []

        resident = []
        for entry in entries:
            expires = entry.get("expires_at")
            resident.append(
                ResidentModel(
                    physical_id=entry.get("name") or entry.get("model", ""),
                    size_gb=float(entry.get("size_vram") or entry.get("size") or 0) / 1e9,
                    expires_at=self._parse_expiry(expires),
                )
            )
        return resident

    @staticmethod
    def _parse_expiry(value: str | None) -> float | None:
        if not value:
            return None
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    async def ensure_loaded(self, model: str) -> None:
        """Load a model without generating anything.

        Ollama treats an empty message list as a pure load request, so warming
        costs nothing beyond the load itself.
        """
        try:
            await self._client.post(
                "/api/chat",
                json={"model": model, "messages": [], "keep_alive": "10m"},
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            log.warning("model_warm_failed", provider=self.name, model=model, error=str(exc))

    async def pin(self, model: str) -> None:
        """Hold a model in memory indefinitely.

        Used for the small utility model behind router stage 2, where a cold
        load would cost more than the classification it is trying to save.
        """
        try:
            await self._client.post(
                "/api/chat",
                json={"model": model, "messages": [], "keep_alive": -1},
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            log.warning("model_pin_failed", provider=self.name, model=model, error=str(exc))

    async def unload(self, model: str) -> None:
        """Evict immediately by setting keep_alive to zero."""
        try:
            await self._client.post(
                "/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            log.warning("model_unload_failed", provider=self.name, model=model, error=str(exc))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------- internals
    @staticmethod
    def _raise_for_status(response: httpx.Response, model: str) -> None:
        if response.status_code < 400:
            return
        body = response.text[:600]
        lowered = body.lower()
        if response.status_code == 404 or "not found" in lowered:
            raise ModelNotFoundError(
                f"ollama does not have model '{model}' — run 'ollama pull {model}'"
            )
        if "context" in lowered and ("length" in lowered or "window" in lowered):
            raise ContextLengthExceededError(f"ollama/{model}: {body}")
        raise ProviderResponseError(f"ollama returned HTTP {response.status_code}: {body}")

    @staticmethod
    def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
        if not raw:
            return []
        calls = []
        for entry in raw:
            function = entry.get("function") or {}
            arguments = function.get("arguments")
            # Ollama usually returns arguments already decoded, unlike OpenAI.
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            calls.append(
                ToolCall(
                    id=entry.get("id") or f"call_{new_id()}",
                    name=function.get("name", ""),
                    arguments=arguments or {},
                )
            )
        return calls
