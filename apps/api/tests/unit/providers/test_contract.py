"""The provider contract suite.

This is the operational definition of "model-agnostic": every backend runs the
same assertions, and a new backend ships when it passes them. The mock provider
always runs; the real ones join in when their environment variable is set, so
the same expectations are checked against actual models nightly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from workbench.providers.base import LLMProvider
from workbench.providers.mock import MockProvider
from workbench.providers.ollama import OllamaProvider
from workbench.providers.openai_compat import OpenAICompatProvider
from workbench.providers.types import ChatMessage, GenerationRequest



def _providers() -> list[pytest.param]:
    """The backends to test, plus the model each should be asked for."""
    cases = [pytest.param("mock", "mock-chat", id="mock")]
    if os.environ.get("OLLAMA_E2E"):
        cases.append(
            pytest.param("ollama", os.environ.get("OLLAMA_E2E_MODEL", "qwen3.5:2b"),
                         id="ollama", marks=pytest.mark.ollama)
        )
        cases.append(
            pytest.param("lmstudio", os.environ.get("LMSTUDIO_E2E_MODEL", "qwen/qwen3-4b"),
                         id="lmstudio", marks=pytest.mark.ollama)
        )
    return cases




def _build(kind: str) -> LLMProvider:
    from pathlib import Path

    if kind == "mock":
        fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "mock_responses.yaml"
        return MockProvider(fixtures_path=fixtures, record_misses=False)
    if kind == "ollama":
        return OllamaProvider(base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    return OpenAICompatProvider(
        name="lmstudio",
        base_url=os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
    )


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_health_never_raises(kind: str, model: str) -> None:
    """An unreachable backend must report unhealthy, not explode.

    Health is polled for the admin status grid; an exception here would take
    down the status page instead of showing a red light.
    """
    provider = _build(kind)
    try:
        health = await provider.health()
        assert health.provider
        assert isinstance(health.healthy, bool)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_generate_returns_text(kind: str, model: str) -> None:
    provider = _build(kind)
    try:
        result = await provider.generate(
            GenerationRequest(
                model=model,
                messages=[ChatMessage(role="user", content="Say the word: ready")],
                temperature=0.0,
                max_tokens=512,
            )
        )
        assert isinstance(result.text, str)
        assert result.text.strip(), "provider returned an empty completion"
        assert result.model_used
        assert result.latency_ms >= 0
    finally:
        await provider.aclose()


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_stream_yields_then_terminates(kind: str, model: str) -> None:
    """Streaming must end with exactly one done chunk carrying metadata.

    Downstream code closes the SSE stream on that chunk; a backend that never
    sends one would hang the browser connection open forever.
    """
    provider = _build(kind)
    try:
        chunks = []
        async for chunk in provider.stream(
            GenerationRequest(
                model=model,
                messages=[ChatMessage(role="user", content="Count: one two three")],
                temperature=0.0,
                max_tokens=512,
            )
        ):
            chunks.append(chunk)

        assert chunks, "stream produced nothing"
        assert chunks[-1].done is True
        assert sum(1 for c in chunks if c.done) == 1, "exactly one terminal chunk expected"
        assert "model_used" in chunks[-1].meta
        text = "".join(c.delta for c in chunks if not c.done)
        assert text.strip(), "stream carried no content"
    finally:
        await provider.aclose()


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_stream_and_generate_agree(kind: str, model: str) -> None:
    """The two code paths must not diverge in what they can produce."""
    provider = _build(kind)
    request = GenerationRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Reply with the single word: consistent")],
        temperature=0.0,
        seed=42,
        max_tokens=512,
    )
    try:
        direct = await provider.generate(request)
        streamed = "".join(
            [chunk.delta async for chunk in provider.stream(request) if not chunk.done]
        )
        assert direct.text.strip()
        assert streamed.strip()
    finally:
        await provider.aclose()


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_embed_shape_is_consistent(kind: str, model: str) -> None:
    """Every vector in a batch must have identical dimensionality.

    A ragged batch corrupts a vector index in a way that only shows up much
    later as inexplicably bad retrieval.
    """
    provider = _build(kind)
    embed_model = {
        "mock": "mock-embed",
        "ollama": os.environ.get("OLLAMA_E2E_EMBED", "nomic-embed-text"),
        "lmstudio": "text-embedding-nomic-embed-text-v1.5",
    }[kind]
    try:
        vectors = await provider.embed(embed_model, ["corrosion rate", "pump vibration", "x"])
        assert len(vectors) == 3
        dims = {len(v) for v in vectors}
        assert len(dims) == 1, f"inconsistent embedding dimensions: {dims}"
        assert dims.pop() > 0
    except Exception as exc:  # noqa: BLE001
        if kind == "mock":
            raise
        pytest.skip(f"{kind} embedding unavailable: {exc}")
    finally:
        await provider.aclose()


@pytest.mark.parametrize(("kind", "model"), _providers())
async def test_embed_empty_batch(kind: str, model: str) -> None:
    """An empty batch must not become a request. Chunking can legitimately
    produce nothing, and that should cost zero round trips."""
    provider = _build(kind)
    try:
        assert await provider.embed("any-model", []) == []
    finally:
        await provider.aclose()


async def test_mock_is_deterministic() -> None:
    """The same request must always produce the same answer.

    Everything downstream — golden files, artifact digests, eval baselines —
    depends on this.
    """
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "mock_responses.yaml"
    request = GenerationRequest(
        model="mock-chat",
        messages=[ChatMessage(role="user", content="anything at all")],
    )
    first = await MockProvider(fixtures_path=fixtures, record_misses=False).generate(request)
    second = await MockProvider(fixtures_path=fixtures, record_misses=False).generate(request)
    assert first.text == second.text


async def test_mock_synthesises_schema_valid_json(mock_provider: MockProvider) -> None:
    """An unknown request with a schema still yields something parseable.

    Structured-output call sites parse and validate the reply, so an unfixtured
    request must fail on content, not on a JSON decode error in unrelated code.
    """
    import json

    schema = {
        "type": "object",
        "properties": {
            "lane": {"type": "string", "enum": ["reasoning", "coding"]},
            "confidence": {"type": "number"},
        },
        "required": ["lane", "confidence"],
    }
    result = await mock_provider.generate(
        GenerationRequest(
            model="mock-chat",
            messages=[ChatMessage(role="user", content="never seen before")],
            json_schema=schema,
        )
    )
    parsed = json.loads(result.text)
    assert parsed["lane"] in {"reasoning", "coding"}
    assert isinstance(parsed["confidence"], (int, float))


async def test_mock_named_fixture(mock_provider: MockProvider) -> None:
    """Named fixtures let a test pin one specific interaction readably."""
    result = await mock_provider.generate(
        GenerationRequest(
            model="mock-chat",
            messages=[ChatMessage(role="user", content="classify this")],
            metadata={"fixture": "router_classify_coding"},
        )
    )
    assert '"lane":"coding"' in result.text
