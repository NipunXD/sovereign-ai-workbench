"""Provider for any OpenAI-compatible endpoint.

Covers two of our three real backends:

* **LM Studio** — the default on Apple Silicon, serving MLX 4-bit weights.
* **vLLM** — the air-gapped GPU server target.

They differ only in details this class parameterises (how structured output is
requested, and whether model residency can be inspected), so one implementation
serves both rather than two near-copies drifting apart.

Nothing here reaches the public internet: ``base_url`` always points at a host
inside the deployment.
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

#: Substrings that identify a context-overflow rejection across backends. These
#: arrive as prose in an error body, so matching text is unavoidable.
_CONTEXT_MARKERS = (
    "context length",
    "context window",
    "maximum context",
    "too many tokens",
    "reduce the length",
)


class OpenAICompatProvider:
    """Talks the OpenAI chat/embeddings wire format."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        timeout_s: float = 300.0,
        api_key: str = "not-needed",
        structured_output_mode: str = "json_schema",
        admin_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.admin_url = admin_url.rstrip("/") if admin_url else None
        #: vLLM wants ``guided_json``; LM Studio wants OpenAI's ``json_schema``.
        self.structured_output_mode = structured_output_mode
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    # ------------------------------------------------------------------ health
    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = await self._client.get("/models", timeout=10.0)
            response.raise_for_status()
            models = response.json().get("data", [])
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                models_available=len(models),
            )
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/models", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"{self.name} is not reachable at {self.base_url}: {exc}"
            ) from exc
        return [entry["id"] for entry in response.json().get("data", [])]

    # ----------------------------------------------------------- payload build
    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        """Render one message, using the multimodal shape only when needed.

        Sending the content-parts array for plain text works on most servers but
        not all; keeping text messages as plain strings is the safer path.
        """
        if not message.images:
            payload: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                payload["tool_call_id"] = message.tool_call_id
            if message.name:
                payload["name"] = message.name
            return payload

        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"type": "text", "text": message.content})
        parts.extend(
            {"type": "image_url", "image_url": {"url": image.to_data_url()}}
            for image in message.images
        )
        return {"role": message.role, "content": parts}

    def _build_payload(self, req: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": [self._message_payload(m) for m in req.messages],
            "temperature": req.temperature,
            "stream": stream,
        }
        if stream:
            # Ask for usage on the final chunk; harmless where unsupported.
            payload["stream_options"] = {"include_usage": True}
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.stop:
            payload["stop"] = req.stop
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.tools:
            payload["tools"] = [tool.to_openai() for tool in req.tools]
        if req.json_schema is not None:
            payload.update(self._structured_output(req.json_schema))
        return payload

    def _structured_output(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Constrain the response to a schema, in this backend's dialect."""
        if self.structured_output_mode == "guided_json":
            return {"extra_body": {"guided_json": schema}}
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }
        }

    # --------------------------------------------------------------- generate
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        payload = self._build_payload(req, stream=False)
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.name} timed out on {req.model}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name} unreachable: {exc}") from exc

        self._raise_for_status(response, req.model)

        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice.get("message", {})
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(
                f"{self.name} returned an unparseable response: {exc}"
            ) from exc

        usage_body = body.get("usage") or {}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return GenerationResult(
            text=message.get("content") or "",
            # Qwen3 and other thinking models return their monologue here.
            reasoning=message.get("reasoning_content") or message.get("reasoning") or "",
            model_used=body.get("model", req.model),
            finish_reason=choice.get("finish_reason") or "stop",
            tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            usage=Usage(
                prompt_tokens=int(usage_body.get("prompt_tokens", 0)),
                completion_tokens=int(usage_body.get("completion_tokens", 0)),
            ),
            latency_ms=elapsed_ms,
            # Without streaming there is no separate first-token moment.
            ttft_ms=elapsed_ms,
        )

    async def stream(self, req: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        started = time.perf_counter()
        ttft_ms = 0
        thinking_ms = 0
        payload = self._build_payload(req, stream=True)
        # Tool calls arrive as fragments spread across chunks and must be
        # reassembled by index before they mean anything.
        partial_tools: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish_reason = "stop"

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, req.model)

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        log.warning("stream_chunk_unparseable", provider=self.name)
                        continue

                    if event_usage := event.get("usage"):
                        usage = Usage(
                            prompt_tokens=int(event_usage.get("prompt_tokens", 0)),
                            completion_tokens=int(event_usage.get("completion_tokens", 0)),
                        )

                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if reason := choice.get("finish_reason"):
                        finish_reason = reason

                    delta = choice.get("delta") or {}
                    self._accumulate_tool_calls(delta.get("tool_calls"), partial_tools)

                    # Reasoning streams on its own channel so it never leaks
                    # into the answer text, and so the UI can show progress
                    # during the long silent think before the first real token.
                    if reasoning := (
                        delta.get("reasoning_content") or delta.get("reasoning")
                    ):
                        yield GenerationChunk(reasoning_delta=reasoning)

                    if text := delta.get("content"):
                        if ttft_ms == 0:
                            ttft_ms = int((time.perf_counter() - started) * 1000)
                            thinking_ms = ttft_ms
                        yield GenerationChunk(delta=text)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.name} timed out on {req.model}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name} unreachable: {exc}") from exc

        yield GenerationChunk(
            done=True,
            tool_calls=self._finalise_tool_calls(partial_tools),
            meta={
                "model_used": req.model,
                "finish_reason": finish_reason,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "ttft_ms": ttft_ms,
                "thinking_ms": thinking_ms,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
        )

    # ---------------------------------------------------------------- embed
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/embeddings", json={"model": model, "input": texts}
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.name} timed out embedding") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name} unreachable: {exc}") from exc

        self._raise_for_status(response, model)
        body = response.json()
        try:
            # Some servers return results out of order; index is authoritative.
            rows = sorted(body["data"], key=lambda row: row.get("index", 0))
            return [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise ProviderResponseError(
                f"{self.name} returned a malformed embedding response"
            ) from exc

    # ------------------------------------------------------------- residency
    async def resident_models(self) -> list[ResidentModel]:
        """Which models are loaded, when the backend can tell us.

        LM Studio exposes this through its native admin API. Plain OpenAI-
        compatible servers do not, so we return nothing and let the residency
        manager fall back to its own accounting.
        """
        if not self.admin_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.admin_url}/models")
                response.raise_for_status()
                entries = response.json().get("data", [])
        except (httpx.HTTPError, ValueError):
            return []

        resident = []
        for entry in entries:
            if entry.get("state") not in {"loaded", "loading"}:
                continue
            size_bytes = entry.get("max_context_length") and entry.get("size_bytes")
            resident.append(
                ResidentModel(
                    physical_id=entry.get("id", ""),
                    size_gb=float(entry.get("size_bytes") or 0) / 1e9 if size_bytes else 0.0,
                )
            )
        return resident

    async def ensure_loaded(self, model: str) -> None:
        """Warm a model with a one-token request.

        There is no portable "load" verb in the OpenAI protocol, and a minimal
        completion costs far less than letting a user's first real request pay
        the whole load latency.
        """
        try:
            await self._client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "."}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                },
                timeout=180.0,
            )
        except httpx.HTTPError as exc:
            log.warning("model_warm_failed", provider=self.name, model=model, error=str(exc))

    async def unload(self, model: str) -> None:
        """Evict a model where the backend supports it."""
        if not self.admin_url:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self.admin_url}/models/{model}/unload")
        except httpx.HTTPError:
            log.debug("model_unload_unsupported", provider=self.name, model=model)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------- internals
    def _raise_for_status(self, response: httpx.Response, model: str) -> None:
        if response.status_code < 400:
            return
        body = response.text[:600]
        lowered = body.lower()
        if response.status_code == 404 or "model_not_found" in lowered:
            raise ModelNotFoundError(f"{self.name} does not have model '{model}'")
        if any(marker in lowered for marker in _CONTEXT_MARKERS):
            raise ContextLengthExceededError(f"{self.name}/{model}: {body}")
        raise ProviderResponseError(f"{self.name} returned HTTP {response.status_code}: {body}")

    @staticmethod
    def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
        if not raw:
            return []
        calls = []
        for entry in raw:
            function = entry.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                # A model that emits malformed arguments should surface as a
                # tool validation failure downstream, not crash the parse here.
                arguments = {"_raw": function.get("arguments", "")}
            calls.append(
                ToolCall(
                    id=entry.get("id") or f"call_{new_id()}",
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )
        return calls

    @staticmethod
    def _accumulate_tool_calls(
        deltas: list[dict[str, Any]] | None, into: dict[int, dict[str, Any]]
    ) -> None:
        """Merge streamed tool-call fragments, which arrive split by index."""
        for delta in deltas or []:
            index = int(delta.get("index", 0))
            slot = into.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call_id := delta.get("id"):
                slot["id"] = call_id
            function = delta.get("function") or {}
            if name := function.get("name"):
                slot["name"] = name
            if arguments := function.get("arguments"):
                slot["arguments"] += arguments

    @staticmethod
    def _finalise_tool_calls(partial: dict[int, dict[str, Any]]) -> list[ToolCall]:
        calls = []
        for index in sorted(partial):
            slot = partial[index]
            if not slot["name"]:
                continue
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": slot["arguments"]}
            calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{new_id()}",
                    name=slot["name"],
                    arguments=arguments,
                )
            )
        return calls
