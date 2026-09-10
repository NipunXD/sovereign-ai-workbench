"""Chunking behaviour.

The failures that matter here are quiet ones: a chunk that overruns the
embedding window is truncated by the backend and its tail becomes unsearchable,
and a table split mid-row produces numbers with no header — actively misleading
rather than merely unhelpful.
"""

from __future__ import annotations

import pytest

from workbench.core.ir import BBox, Block, BlockSource, BlockType, DocumentIR, Page
from workbench.rag.chunker import Chunker, ChunkSpec, estimate_tokens


def block(
    block_id: str,
    text: str,
    *,
    type: BlockType = BlockType.PARAGRAPH,
    order: int = 0,
    page: int = 1,
    level: int | None = None,
    confidence: float = 1.0,
    source: BlockSource = BlockSource.NATIVE,
) -> Block:
    return Block(
        block_id=block_id,
        page_no=page,
        type=type,
        text=text,
        bbox=BBox(),
        order=order,
        confidence=confidence,
        source=source,
        attrs={"level": level} if level else {},
    )


def document(*blocks: Block, doc_id: str = "doc_test") -> DocumentIR:
    pages: dict[int, list[Block]] = {}
    for b in blocks:
        pages.setdefault(b.page_no, []).append(b)
    return DocumentIR(
        doc_id=doc_id,
        page_count=len(pages),
        pages=[
            Page(page_no=n, width=1000, height=1000, blocks=bs) for n, bs in sorted(pages.items())
        ],
    )


# --- the hard ceiling --------------------------------------------------------
def test_spec_rejects_child_budget_above_embedding_window() -> None:
    """Catch the misconfiguration at construction, not as bad retrieval later."""
    with pytest.raises(ValueError, match="silently truncated"):
        ChunkSpec(child_tokens=4000, max_child_tokens=1800)


def test_no_chunk_exceeds_the_embedding_window() -> None:
    """The invariant that keeps text from silently vanishing from the index."""
    long_prose = " ".join(f"Sentence number {i} about vessel V-1201." for i in range(400))
    chunks = Chunker(ChunkSpec(child_tokens=200, parent_tokens=600, max_child_tokens=1800)).chunk(
        document(block("b1", long_prose))
    )
    assert chunks
    for chunk in chunks:
        assert chunk.token_count <= 1800, f"chunk {chunk.chunk_id} would be truncated"


# --- structure ---------------------------------------------------------------
def test_section_path_is_carried_into_chunks() -> None:
    """A fragment without its heading is not interpretable."""
    chunks = Chunker().chunk(
        document(
            block("h1", "5. Shutdown Procedure", type=BlockType.HEADING, order=0, level=1),
            block("h2", "5.2 Depressurisation", type=BlockType.HEADING, order=1, level=2),
            block("p1", "Hold for four hours at 120 degC.", order=2),
        )
    )
    assert chunks
    assert chunks[0].section_path == ["5. Shutdown Procedure", "5.2 Depressurisation"]
    assert "5.2 Depressurisation" in chunks[0].contextualised_text


def test_heading_stack_pops_to_the_right_level() -> None:
    """A new level-2 heading replaces the previous one, not the level-1 above."""
    chunks = Chunker().chunk(
        document(
            block("h1", "5. Shutdown", type=BlockType.HEADING, order=0, level=1),
            block("h2", "5.1 Cooling", type=BlockType.HEADING, order=1, level=2),
            block("p1", "Cool at 30 degC per hour.", order=2),
            block("h3", "5.2 Purging", type=BlockType.HEADING, order=3, level=2),
            block("p2", "Purge with nitrogen.", order=4),
        )
    )
    paths = [c.section_path for c in chunks]
    assert ["5. Shutdown", "5.1 Cooling"] in paths
    assert ["5. Shutdown", "5.2 Purging"] in paths
    # The level-1 heading must not have been popped by its own subsection.
    assert all(p[0] == "5. Shutdown" for p in paths if p)


def test_headers_and_footers_are_dropped() -> None:
    """Running furniture repeats on every page and dilutes every chunk."""
    chunks = Chunker().chunk(
        document(
            block("hd", "MRPL CONFIDENTIAL", type=BlockType.HEADER, order=0),
            block("p1", "The vessel was inspected in March.", order=1),
            block("ft", "Page 3 of 12", type=BlockType.FOOTER, order=2),
        )
    )
    combined = " ".join(c.text for c in chunks)
    assert "inspected in March" in combined
    assert "MRPL CONFIDENTIAL" not in combined
    assert "Page 3 of 12" not in combined


# --- tables ------------------------------------------------------------------
def test_oversized_table_splits_on_rows_and_repeats_the_header() -> None:
    """Numbers without their column header are worse than no data at all."""
    header = "CML | 2023_mm | 2024_mm | 2025_mm"
    rows = [f"CML-{i:02d} | 12.{i} | 11.{i} | 10.{i}" for i in range(1, 60)]
    table = block("t1", "\n".join([header, *rows]), type=BlockType.TABLE)

    chunks = Chunker(ChunkSpec(child_tokens=120, parent_tokens=200)).chunk(document(table))
    table_chunks = [c for c in chunks if "CML-" in c.text]
    assert len(table_chunks) > 1, "table should have been split"
    for chunk in table_chunks:
        assert chunk.text.startswith(header), "every piece must carry the header"
        for line in chunk.text.splitlines()[1:]:
            # A split mid-row would leave a line without its full field count.
            assert line.count("|") == header.count("|"), f"row was split: {line!r}"


def test_small_table_is_left_intact() -> None:
    table = block("t1", "CML | mm\nCML-01 | 9.2\nCML-02 | 9.4", type=BlockType.TABLE)
    chunks = Chunker().chunk(document(table))
    assert len(chunks) == 1
    assert "CML-02" in chunks[0].text


# --- parent/child ------------------------------------------------------------
def test_short_section_is_not_duplicated_as_parent_and_child() -> None:
    """Indexing the same text twice returns it twice under different ids."""
    chunks = Chunker(ChunkSpec(child_tokens=256, parent_tokens=900)).chunk(
        document(block("p1", "A short paragraph about the pump."))
    )
    assert len(chunks) == 1
    assert chunks[0].parent_id is None
    assert chunks[0].is_parent is False


def test_long_section_produces_parent_and_children() -> None:
    long_text = " ".join(f"Observation {i} recorded during the inspection." for i in range(200))
    chunks = Chunker(ChunkSpec(child_tokens=100, parent_tokens=400)).chunk(
        document(block("p1", long_text))
    )
    parents = [c for c in chunks if c.is_parent]
    children = [c for c in chunks if c.parent_id]
    assert parents and children
    assert {c.parent_id for c in children} <= {p.chunk_id for p in parents}


# --- confidence propagation --------------------------------------------------
def test_chunk_confidence_is_word_weighted() -> None:
    """A short high-confidence caption must not mask a struggling paragraph.

    This value ends up on the citation, so a user can see when an answer rests
    on poorly-recognised text.
    """
    chunks = Chunker().chunk(
        document(
            block("b1", " ".join(["barely"] * 50), confidence=0.50, source=BlockSource.OCR),
            block("b2", "ok", confidence=1.0, source=BlockSource.OCR, order=1),
        )
    )
    assert chunks
    # Naive averaging would give 0.75; word-weighted stays near the long block.
    assert chunks[0].mean_confidence < 0.55


def test_estimate_tokens_is_conservative() -> None:
    """Undercounting risks silent truncation, so the estimate must not run low."""
    text = "The vessel shell thickness was measured at twelve locations."
    assert estimate_tokens(text) >= len(text.split())
