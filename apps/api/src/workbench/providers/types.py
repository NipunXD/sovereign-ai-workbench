"""Provider-facing data types.

These are the vocabulary every call site uses. Nothing above this layer knows
whether a request ends up at LM Studio, Ollama, vLLM or a fixture file — which
is what makes "model-agnostic" a property of the code rather than a claim in a
slide deck.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Capability(StrEnum):
    """What a model can do. The router filters candidates on these."""

    CHAT = "chat"
    TOOLS = "tools"
    JSON = "json"
    VISION = "vision"
    EMBED = "embed"
    LONG_CTX = "long_ctx"


class Lane(StrEnum):
    """Task categories the router chooses between."""

    REASONING = "reasoning"
    CODING = "coding"
    LONG_CTX = "long_ctx"
    VISION = "vision"
    EMBEDDING = "embedding"
    UTILITY = "utility"


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One entry from config/models.yaml, resolved."""

    logical_name: str
    provider: str
    physical_id: str
    capabilities: frozenset[Capability]
    context_window: int
    approx_ram_gb: float
    quality_tier: int = 2
    speed_tier: int = 2
    default_num_ctx: int | None = None
    dimensions: int | None = None
    pinned: bool = False
    optional: bool = False

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def supports_all(self, capabilities: frozenset[Capability]) -> bool:
        return capabilities <= self.capabilities


@dataclass(frozen=True, slots=True)
class ImageRef:
    """An image attached to a message.

    Carries raw bytes rather than a path so providers that need base64 (all of
    them, currently) do not each reimplement file reading and MIME sniffing.
    """

    data: bytes
    mime_type: str = "image/png"
    #: Where this came from, for citation and audit purposes.
    source_ref: str | None = None

    def to_base64(self) -> str:
        import base64

        return base64.b64encode(self.data).decode("ascii")

    def to_data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.to_base64()}"


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    images: list[ImageRef] = field(default_factory=list)
    #: Set on tool-result messages so the model can correlate them.
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """A tool description in the shape model APIs expect."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class GenerationRequest:
    """One inference request, expressed in logical terms.

    ``model`` is a *logical* name such as ``reasoning.primary``. The registry
    resolves it to a physical model on a specific provider.
    """

    model: str
    messages: list[ChatMessage]
    temperature: float = 0.2
    max_tokens: int | None = None
    #: When set, the provider constrains output to this JSON schema.
    json_schema: dict[str, Any] | None = None
    tools: list[ToolSchema] | None = None
    stop: list[str] | None = None
    seed: int | None = None
    num_ctx: int | None = None
    #: Provider-specific idle-eviction hint (Ollama's keep_alive).
    keep_alive: str | None = None
    request_id: str = ""
    #: Free-form routing/telemetry context; never sent to the model.
    metadata: dict[str, Any] = field(default_factory=dict)

    def estimated_prompt_tokens(self) -> int:
        """Cheap size estimate used by the router before a tokenizer exists.

        Roughly four characters per token for English technical prose, plus a
        flat allowance per image since a vision encoder turns one picture into
        several hundred tokens regardless of its byte size.
        """
        chars = sum(len(m.content) for m in self.messages)
        images = sum(len(m.images) for m in self.messages)
        return chars // 4 + images * 800


@dataclass(frozen=True, slots=True)
class GenerationChunk:
    """One increment of a streaming response.

    ``delta`` and ``reasoning_delta`` are kept strictly separate. Thinking
    models emit a long internal monologue before their answer; presenting that
    as the answer would be wrong, but discarding it loses the most interesting
    part of an agent trace. So it streams on its own channel and the UI renders
    it as a collapsible "thinking" panel.
    """

    delta: str = ""
    reasoning_delta: str = ""
    done: bool = False
    #: Populated on the final chunk when the model emitted tool calls.
    tool_calls: list[ToolCall] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """A completed (non-streaming, or fully accumulated) response."""

    text: str
    model_used: str
    #: The model's internal reasoning, when it emits any. Never part of `text`.
    reasoning: str = ""
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    #: Time to the first *content* token — the number a user actually feels.
    #: On a thinking model this trails the first emitted token considerably.
    ttft_ms: int = 0
    #: Milliseconds spent reasoning before any answer text appeared.
    thinking_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    detail: str = ""
    latency_ms: int = 0
    models_available: int = 0


@dataclass(frozen=True, slots=True)
class ResidentModel:
    """A model currently held in memory by a provider."""

    physical_id: str
    size_gb: float
    #: Unix timestamp when the provider intends to evict it, when known.
    expires_at: float | None = None
