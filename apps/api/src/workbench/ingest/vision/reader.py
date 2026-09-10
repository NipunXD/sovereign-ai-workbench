"""Reading pages and drawings with a vision model.

Two jobs, with different failure modes:

* **Page rescue.** OCR produced nothing usable — a bad scan, handwriting, an
  unusual layout. The VLM transcribes what it can see. Output is marked
  ``source=vlm`` and given a deliberately capped confidence, because a vision
  model reading a bad scan is a plausible transcription, not a measurement.

* **Drawing parsing.** A P&ID is tiled, because VLMs degrade badly on a whole
  sheet — small tag text disappears into the resize. Every tag the model reports
  is then cross-checked against OCR and the tag registry, and anything
  unconfirmed is marked rather than asserted. A hallucinated valve number on an
  isolation drawing is the single most dangerous output this system could
  produce.

Measured on the seed P&ID (2200x1500, nine tags, qwen3-vl on an M4):

    whole image, no tiling      0% recall    138s
    2 tiles at 1600px          22% recall    158s
    6 tiles at 1024px          67% recall    297s   <- configured
    6 tiles at 1024px, 4B      22% recall    319s

Tiling is doing real work, not adding overhead: the model simply cannot resolve
tag text at full-sheet scale. The smaller vision model is no faster and much
worse, so there is no cheap win available. Precision was 100% in every
configuration — nothing was invented — which is the property that matters most
here, and it is what makes 67% recall usable: the answer cites the tags it
found and does not fabricate the ones it missed.

At roughly five minutes per drawing this belongs in background ingestion, not
on an interactive path.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from workbench.core.logging import get_logger
from workbench.ingest.ir import BBox, Block, BlockSource, BlockType

log = get_logger(__name__)

#: A vision model's transcription is never treated as exact, however sure it
#: sounds. This caps the confidence recorded for VLM-sourced text.
VLM_CONFIDENCE_CAP = 0.75

#: Confidence recorded for a tag the model reported but nothing else confirms.
UNCONFIRMED_TAG_CONFIDENCE = 0.35

TRANSCRIBE_PROMPT = """\
Transcribe all text visible in this page image, preserving reading order and \
line breaks. Include handwritten annotations, stamps and marginal notes, \
marking each as [handwritten: ...].

Transcribe only what is actually legible. Where text cannot be read, write \
[illegible] rather than guessing — a plausible invention is worse than a gap.

Output the transcription only, with no commentary."""

DRAWING_PROMPT = """\
This is a section of an engineering drawing (P&ID or similar).

List every equipment tag and instrument tag you can read, with the type of item \
it labels and any connection you can see. Tags look like V-1201, P-101A, \
HV-1205, PT-3402.

Report only tags you can actually read in the image. Do not infer a tag from \
context or complete a partially visible one.

Respond with JSON only."""

DRAWING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "equipment_type": {"type": "string"},
                    "connected_to": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["tag", "equipment_type"],
            },
        }
    },
    "required": ["tags"],
}


@dataclass
class DrawingTag:
    tag: str
    equipment_type: str = ""
    connected_to: list[str] = field(default_factory=list)
    note: str = ""
    #: Where the tag was corroborated: "ocr", "registry", or empty if nowhere.
    confirmed_by: list[str] = field(default_factory=list)
    tile: int = 0

    @property
    def confirmed(self) -> bool:
        return bool(self.confirmed_by)


class VisionReader:
    """Reads page images and drawings with the vision lane."""

    def __init__(
        self,
        *,
        registry: Any,
        residency: Any = None,
        tag_pattern: str = r"^[A-Z]{1,3}-?[0-9]{3,5}[A-Z]?$",
        tile_px: int = 1024,
        overlap_px: int = 128,
    ) -> None:
        self.registry = registry
        self.residency = residency
        self.tag_regex = re.compile(tag_pattern)
        self.tile_px = tile_px
        self.overlap_px = overlap_px

    async def transcribe_page(
        self, image_png: bytes, page_no: int
    ) -> tuple[list[Block], float]:
        """Rescue a page OCR could not read."""
        from workbench.providers.types import ChatMessage, GenerationRequest, ImageRef

        text = await self._ask(
            TRANSCRIBE_PROMPT, image_png, max_tokens=2048
        )
        if not text.strip():
            return [], 0.0

        blocks: list[Block] = []
        for order, paragraph in enumerate(p for p in re.split(r"\n\s*\n", text) if p.strip()):
            stripped = paragraph.strip()
            is_handwriting = "[handwritten:" in stripped.lower()
            # An [illegible] marker means the model itself flagged uncertainty;
            # record that rather than averaging it away.
            illegible = "[illegible]" in stripped.lower()
            blocks.append(
                Block(
                    block_id=f"p{page_no}v{order}",
                    page_no=page_no,
                    type=BlockType.HANDWRITING if is_handwriting else BlockType.PARAGRAPH,
                    text=stripped,
                    # The VLM does not return coordinates for prose, so the box
                    # covers the page; the viewer highlights the page, not a line.
                    bbox=BBox(),
                    confidence=VLM_CONFIDENCE_CAP * (0.7 if illegible else 1.0),
                    source=BlockSource.VLM,
                    order=order,
                    attrs={"vlm_transcribed": True, "has_illegible": illegible},
                )
            )
        return blocks, VLM_CONFIDENCE_CAP

    async def parse_drawing(
        self,
        image: np.ndarray,
        page_no: int,
        *,
        ocr_text: str = "",
        known_tags: frozenset[str] = frozenset(),
    ) -> list[Block]:
        """Tile a drawing, extract tags, and cross-check every one of them."""
        import json

        started = time.perf_counter()
        tiles = self._tile(image)
        log.info("drawing_tiled", page=page_no, tiles=len(tiles))

        # Tags OCR independently found in the raw text, used as corroboration.
        ocr_tags = {
            token.upper()
            for token in re.findall(r"[A-Z]{1,3}-?\d{3,5}[A-Z]?", ocr_text.upper())
        }

        found: dict[str, DrawingTag] = {}
        for index, (tile_image, _) in enumerate(tiles):
            png = self._encode(tile_image)
            raw = await self._ask(DRAWING_PROMPT, png, json_schema=DRAWING_SCHEMA, max_tokens=1024)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for entry in payload.get("tags") or []:
                tag = str(entry.get("tag", "")).strip().upper()
                if not tag or not self.tag_regex.match(tag):
                    continue
                existing = found.get(tag)
                if existing is None:
                    found[tag] = DrawingTag(
                        tag=tag,
                        equipment_type=str(entry.get("equipment_type", "")),
                        connected_to=[str(c).upper() for c in entry.get("connected_to") or []],
                        note=str(entry.get("note", "")),
                        tile=index,
                    )
                else:
                    existing.connected_to.extend(
                        str(c).upper() for c in entry.get("connected_to") or []
                    )

        # Cross-validation. A tag the model alone reports is not trusted.
        for tag_name, tag in found.items():
            normalised = tag_name.replace("-", "")
            if tag_name in ocr_tags or normalised in {t.replace("-", "") for t in ocr_tags}:
                tag.confirmed_by.append("ocr")
            if tag_name in known_tags or normalised in {t.replace("-", "") for t in known_tags}:
                tag.confirmed_by.append("registry")

        confirmed = sum(1 for t in found.values() if t.confirmed)
        log.info(
            "drawing_parsed",
            page=page_no,
            tags_found=len(found),
            confirmed=confirmed,
            unconfirmed=len(found) - confirmed,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

        blocks: list[Block] = []
        for order, tag in enumerate(sorted(found.values(), key=lambda t: t.tag)):
            connections = ", ".join(sorted(set(tag.connected_to)))
            suffix = "" if tag.confirmed else "  [UNCONFIRMED — read by the vision model only]"
            blocks.append(
                Block(
                    block_id=f"p{page_no}tag{order}",
                    page_no=page_no,
                    type=BlockType.DRAWING_ANNOTATION,
                    text=(
                        f"{tag.tag} — {tag.equipment_type}"
                        + (f", connected to {connections}" if connections else "")
                        + (f". {tag.note}" if tag.note else "")
                        + suffix
                    ),
                    confidence=VLM_CONFIDENCE_CAP if tag.confirmed else UNCONFIRMED_TAG_CONFIDENCE,
                    source=BlockSource.VLM,
                    order=order,
                    attrs={
                        "tag": tag.tag,
                        "equipment_type": tag.equipment_type,
                        "connected_to": sorted(set(tag.connected_to)),
                        "confirmed_by": tag.confirmed_by,
                        "confirmed": tag.confirmed,
                    },
                )
            )
        return blocks

    # ------------------------------------------------------------- internals
    async def _ask(
        self,
        prompt: str,
        image_png: bytes,
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> str:
        from workbench.providers.types import ChatMessage, GenerationRequest, ImageRef

        info = self.registry.get_model("vision.primary")
        if self.residency is not None:
            await self.residency.acquire("vision.primary")
        provider = self.registry.get_provider(info.provider)
        try:
            result = await provider.generate(
                GenerationRequest(
                    model=info.physical_id,
                    messages=[
                        ChatMessage(
                            role="user",
                            content=prompt,
                            images=[ImageRef(data=image_png, mime_type="image/png")],
                        )
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                    num_ctx=info.default_num_ctx,
                    json_schema=json_schema,
                )
            )
            return result.text
        except Exception as exc:  # noqa: BLE001 - a VLM failure must not fail ingestion
            log.warning("vision_read_failed", error=str(exc))
            return ""

    def _tile(self, image: np.ndarray) -> list[tuple[np.ndarray, tuple[int, int]]]:
        """Cut an image into overlapping tiles.

        Overlap matters: a tag sitting exactly on a tile boundary would
        otherwise be split in half and read as two different numbers.
        """
        height, width = image.shape[:2]
        if height <= self.tile_px and width <= self.tile_px:
            return [(image, (0, 0))]

        stride = self.tile_px - self.overlap_px
        tiles: list[tuple[np.ndarray, tuple[int, int]]] = []
        for top in range(0, height, stride):
            for left in range(0, width, stride):
                bottom = min(top + self.tile_px, height)
                right = min(left + self.tile_px, width)
                if bottom - top < 64 or right - left < 64:
                    continue
                tiles.append((image[top:bottom, left:right], (left, top)))
                if right >= width:
                    break
            if bottom >= height:
                break
        return tiles

    @staticmethod
    def _encode(image: np.ndarray) -> bytes:
        import cv2

        ok, buffer = cv2.imencode(".png", image)
        return bytes(buffer) if ok else b""
