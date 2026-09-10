"""Structure-aware chunking.

Small-to-big: short child chunks are embedded and searched, but the *parent*
window around a hit is what reaches the model. Small chunks retrieve precisely;
large chunks answer well. Splitting the two gets both.

Constraints that shape the implementation:

* The embedding model's context window is the hard ceiling on a child chunk.
  With nomic-embed at 2,048 tokens, a chunk that overruns is silently truncated
  by the backend and the tail becomes unsearchable — a failure that shows up
  much later as inexplicably poor retrieval.
* Table rows are never split. Half a row of thickness readings is worse than
  useless; it is misleading.
* Section headings are carried into every descendant chunk. A retrieved
  fragment saying "hold for 4 hours" means nothing without "5.2 Depressurisation"
  above it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from workbench.core.ids import prefixed_id
from workbench.core.ir import Block, BlockType, DocumentIR

#: Characters per token for English technical prose. Deliberately conservative:
#: undercounting risks silent truncation at the embedding backend, which is a
#: far worse failure than a slightly small chunk.
CHARS_PER_TOKEN = 3.6


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. No tokenizer, no model load."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    """Chunking parameters, mirroring config/ingest.yaml."""

    child_tokens: int = 256
    parent_tokens: int = 900
    overlap_tokens: int = 32
    respect_headings: bool = True
    never_split_table_rows: bool = True
    spreadsheet_rows_per_chunk: int = 20
    #: Hard ceiling from the embedding model's context window.
    max_child_tokens: int = 1800

    def __post_init__(self) -> None:
        if self.child_tokens > self.max_child_tokens:
            raise ValueError(
                f"child_tokens ({self.child_tokens}) exceeds the embedding model's "
                f"usable context ({self.max_child_tokens}); chunks would be "
                f"silently truncated at the backend"
            )


@dataclass
class Chunk:
    """A passage ready to be embedded and indexed."""

    chunk_id: str
    doc_id: str
    text: str
    ordinal: int
    parent_id: str | None = None
    page_from: int = 1
    page_to: int = 1
    block_ids: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    token_count: int = 0
    mean_confidence: float = 1.0
    is_parent: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def contextualised_text(self) -> str:
        """The text as embedded, prefixed with its section path.

        A fragment reading "hold for 4 hours at 120 degC" is ambiguous on its
        own; prefixed with "Shutdown > Depressurisation" it is retrievable by a
        question about either.
        """
        if not self.section_path:
            return self.text
        return f"[{' > '.join(self.section_path)}]\n{self.text}"


class Chunker:
    """Turns a DocumentIR into parent and child chunks."""

    def __init__(self, spec: ChunkSpec | None = None) -> None:
        self.spec = spec or ChunkSpec()

    def chunk(self, ir: DocumentIR) -> list[Chunk]:
        """Chunk a whole document. Parents first, then their children."""
        chunks: list[Chunk] = []
        ordinal = 0

        for section_path, blocks in self._sections(ir):
            for parent_blocks in self._pack(blocks, self.spec.parent_tokens):
                parent = self._make_chunk(
                    ir.doc_id, parent_blocks, section_path, ordinal, is_parent=True
                )
                if not parent.text.strip():
                    continue
                chunks.append(parent)
                ordinal += 1

                children = list(self._pack(parent_blocks, self.spec.child_tokens))
                # A parent that already fits inside a child budget would produce
                # one identical child; indexing both just returns the same text
                # twice under two ids.
                if len(children) == 1 and parent.token_count <= self.spec.child_tokens:
                    chunks[-1].is_parent = False
                    continue

                for child_blocks in children:
                    child = self._make_chunk(
                        ir.doc_id, child_blocks, section_path, ordinal, parent_id=parent.chunk_id
                    )
                    if child.text.strip():
                        chunks.append(child)
                        ordinal += 1

        return chunks

    # ------------------------------------------------------------- sections
    def _sections(self, ir: DocumentIR) -> list[tuple[list[str], list[Block]]]:
        """Group blocks under their heading path.

        Headings form a stack: a level-2 heading replaces the previous level 2
        and everything below it, which reconstructs document structure from a
        flat block list.
        """
        if not self.spec.respect_headings:
            return [([], [b for b in ir.blocks if self._is_content(b)])]

        sections: list[tuple[list[str], list[Block]]] = []
        heading_stack: list[tuple[int, str]] = []
        current: list[Block] = []

        def flush() -> None:
            if current:
                sections.append(([title for _, title in heading_stack], list(current)))
                current.clear()

        for page in ir.pages:
            for block in sorted(page.blocks, key=lambda b: b.order):
                if block.type is BlockType.HEADING:
                    flush()
                    level = int(block.attrs.get("level", 1))
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, block.text.strip()))
                    continue
                if self._is_content(block):
                    current.append(block)
        flush()

        return sections or [([], [b for b in ir.blocks if self._is_content(b)])]

    @staticmethod
    def _is_content(block: Block) -> bool:
        """Running headers and footers repeat on every page and add nothing."""
        return block.type not in (BlockType.HEADER, BlockType.FOOTER) and bool(block.text.strip())

    # ---------------------------------------------------------------- packing
    def _pack(self, blocks: list[Block], budget: int) -> list[list[Block]]:
        """Group blocks into runs that fit the token budget.

        A block larger than the budget on its own is split internally rather
        than emitted oversized — except a table, which is split by rows.
        """
        groups: list[list[Block]] = []
        current: list[Block] = []
        current_tokens = 0

        for block in blocks:
            block_tokens = estimate_tokens(block.text)

            if block_tokens > budget:
                if current:
                    groups.append(current)
                    current, current_tokens = [], 0
                groups.extend([piece] for piece in self._split_oversized(block, budget))
                continue

            if current_tokens + block_tokens > budget and current:
                groups.append(current)
                # Carry the tail of the previous group forward so a sentence
                # spanning a boundary is still retrievable from both sides.
                overlap = self._overlap_blocks(current)
                current = [*overlap, block]
                current_tokens = sum(estimate_tokens(b.text) for b in current)
                continue

            current.append(block)
            current_tokens += block_tokens

        if current:
            groups.append(current)
        return groups

    def _overlap_blocks(self, group: list[Block]) -> list[Block]:
        """The trailing blocks that carry into the next group."""
        if not self.spec.overlap_tokens:
            return []
        carried: list[Block] = []
        total = 0
        for block in reversed(group):
            tokens = estimate_tokens(block.text)
            if total + tokens > self.spec.overlap_tokens:
                break
            carried.insert(0, block)
            total += tokens
        return carried

    def _split_oversized(self, block: Block, budget: int) -> list[Block]:
        """Break a single over-budget block into pieces."""
        if block.type is BlockType.TABLE and self.spec.never_split_table_rows:
            return self._split_table(block, budget)
        return self._split_prose(block, budget)

    def _split_table(self, block: Block, budget: int) -> list[Block]:
        """Split a table by rows, repeating the header on every piece.

        A row group without its header is a grid of numbers with no meaning, so
        the header is duplicated rather than saved.
        """
        rows = [row for row in block.text.splitlines() if row.strip()]
        if len(rows) <= 1:
            return [block]

        header, body = rows[0], rows[1:]
        header_tokens = estimate_tokens(header)
        pieces: list[Block] = []
        current: list[str] = []
        current_tokens = header_tokens

        for row in body:
            row_tokens = estimate_tokens(row)
            if current and current_tokens + row_tokens > budget:
                pieces.append(self._table_piece(block, header, current, len(pieces)))
                current, current_tokens = [], header_tokens
            current.append(row)
            current_tokens += row_tokens

        if current:
            pieces.append(self._table_piece(block, header, current, len(pieces)))
        return pieces or [block]

    @staticmethod
    def _table_piece(block: Block, header: str, rows: list[str], index: int) -> Block:
        return block.model_copy(
            update={
                "block_id": f"{block.block_id}#r{index}",
                "text": "\n".join([header, *rows]),
                "attrs": {**block.attrs, "table_part": index, "header_repeated": True},
            }
        )

    def _split_prose(self, block: Block, budget: int) -> list[Block]:
        """Split prose on sentence boundaries, falling back to whitespace."""
        sentences = re.split(r"(?<=[.!?])\s+", block.text)
        pieces: list[Block] = []
        current: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            tokens = estimate_tokens(sentence)
            if tokens > budget:
                # A single "sentence" this long is usually an unpunctuated OCR
                # run; fall back to a hard word-count split.
                if current:
                    pieces.append(self._prose_piece(block, current, len(pieces)))
                    current, current_tokens = [], 0
                pieces.extend(
                    self._prose_piece(block, [part], len(pieces) + offset)
                    for offset, part in enumerate(self._hard_split(sentence, budget))
                )
                continue
            if current and current_tokens + tokens > budget:
                pieces.append(self._prose_piece(block, current, len(pieces)))
                current, current_tokens = [], 0
            current.append(sentence)
            current_tokens += tokens

        if current:
            pieces.append(self._prose_piece(block, current, len(pieces)))
        return pieces or [block]

    @staticmethod
    def _hard_split(text: str, budget: int) -> list[str]:
        words = text.split()
        per_piece = max(1, int(budget * CHARS_PER_TOKEN / 6))  # ~6 chars/word
        return [" ".join(words[i : i + per_piece]) for i in range(0, len(words), per_piece)] or [
            text
        ]

    @staticmethod
    def _prose_piece(block: Block, sentences: list[str], index: int) -> Block:
        return block.model_copy(
            update={
                "block_id": f"{block.block_id}#p{index}",
                "text": " ".join(sentences).strip(),
            }
        )

    # ----------------------------------------------------------------- build
    def _make_chunk(
        self,
        doc_id: str,
        blocks: list[Block],
        section_path: list[str],
        ordinal: int,
        *,
        parent_id: str | None = None,
        is_parent: bool = False,
    ) -> Chunk:
        text = "\n".join(b.text for b in blocks).strip()
        pages = [b.page_no for b in blocks] or [1]
        # Weight confidence by words so a short caption cannot lift the score of
        # a paragraph the recogniser struggled with.
        weighted = [(b.confidence, b.word_count) for b in blocks if b.word_count]
        total_words = sum(w for _, w in weighted)
        confidence = sum(c * w for c, w in weighted) / total_words if total_words else 1.0

        return Chunk(
            chunk_id=prefixed_id("chunk"),
            doc_id=doc_id,
            text=text,
            ordinal=ordinal,
            parent_id=parent_id,
            page_from=min(pages),
            page_to=max(pages),
            block_ids=[b.block_id for b in blocks],
            section_path=list(section_path),
            token_count=estimate_tokens(text),
            mean_confidence=confidence,
            is_parent=is_parent,
            attrs={
                "block_types": sorted({b.type.value for b in blocks}),
                "sources": sorted({b.source.value for b in blocks}),
            },
        )
