"""Text embedding.

Two details here are load-bearing:

* **The collection name encodes the model and dimensionality.** Swapping the
  embedding model provisions a new collection rather than writing incompatible
  vectors into the existing one. Mixing embedding spaces does not raise an
  error — it silently returns nonsense, which is far harder to notice.
* **Batches are sized against the model's real context window.** nomic-embed
  accepts 2,048 tokens; anything longer is truncated by the backend without
  complaint, so oversized text is split here where it can be handled honestly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from workbench.core.logging import get_logger
from workbench.providers.base import LLMProvider
from workbench.providers.types import ModelInfo
from workbench.rag.chunker import estimate_tokens

log = get_logger(__name__)

#: Requests per batch. Larger batches are faster but a single failure costs
#: more work, and some backends cap the payload size.
DEFAULT_BATCH_SIZE = 16


def collection_name(model: ModelInfo) -> str:
    """A collection name that changes when the embedding space changes.

    ``chunks__nomic_embed_text_v1_5__768``. Two models with the same dimension
    still produce incompatible vectors, so the model identity is part of the
    name, not just the width.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", model.physical_id.lower()).strip("_")
    dimensions = model.dimensions or 0
    return f"chunks__{slug}__{dimensions}"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int
    #: Indices of inputs that had to be truncated to fit the context window.
    truncated: list[int]


class Embedder:
    """Embeds text with the manifest's embedding model."""

    def __init__(
        self,
        provider: LLMProvider,
        model: ModelInfo,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.provider = provider
        self.model = model
        self.batch_size = batch_size
        # Leave headroom: the estimate is approximate and a backend-side
        # truncation is silent, so err on the side of a slightly short input.
        self.max_tokens = max(64, int(model.context_window * 0.9))

    @property
    def collection(self) -> str:
        return collection_name(self.model)

    @property
    def dimensions(self) -> int:
        return self.model.dimensions or 0

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed a list of texts, preserving order."""
        if not texts:
            return EmbeddingResult([], self.model.physical_id, self.dimensions, [])

        prepared: list[str] = []
        truncated: list[int] = []
        for index, text in enumerate(texts):
            fitted, was_truncated = self._fit(text)
            prepared.append(fitted)
            if was_truncated:
                truncated.append(index)

        if truncated:
            log.warning(
                "embedding_input_truncated",
                count=len(truncated),
                max_tokens=self.max_tokens,
                hint="reduce chunk size in config/ingest.yaml",
            )

        vectors: list[list[float]] = []
        for start in range(0, len(prepared), self.batch_size):
            batch = prepared[start : start + self.batch_size]
            vectors.extend(await self.provider.embed(self.model.physical_id, batch))

        if len(vectors) != len(texts):
            raise ValueError(
                f"embedding backend returned {len(vectors)} vectors for {len(texts)} inputs"
            )

        widths = {len(v) for v in vectors}
        if len(widths) > 1:
            # A ragged batch corrupts an index in a way that only surfaces later
            # as unexplainably poor retrieval.
            raise ValueError(f"embedding backend returned inconsistent dimensions: {widths}")

        actual = widths.pop() if widths else 0
        if self.dimensions and actual != self.dimensions:
            raise ValueError(
                f"model '{self.model.logical_name}' produced {actual}-dimensional vectors "
                f"but the manifest declares {self.dimensions}; the collection would be "
                f"unusable. Correct 'dimensions' in config/models.yaml."
            )

        return EmbeddingResult(vectors, self.model.physical_id, actual, truncated)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single search query."""
        result = await self.embed([query])
        return result.vectors[0] if result.vectors else []

    def _fit(self, text: str) -> tuple[str, bool]:
        """Trim text to the model's window, on a word boundary."""
        if estimate_tokens(text) <= self.max_tokens:
            return text, False
        from workbench.rag.chunker import CHARS_PER_TOKEN

        limit = int(self.max_tokens * CHARS_PER_TOKEN)
        cut = text[:limit]
        # Cutting mid-word produces a fragment that embeds oddly; back up to
        # the last space when one is close enough to be worth it.
        if (space := cut.rfind(" ")) > limit * 0.8:
            cut = cut[:space]
        return cut, True
