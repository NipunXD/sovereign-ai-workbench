#!/usr/bin/env python3
"""Simulate scanning, so the corpus has realistic degraded documents.

The seed corpus is generated from known text and then run through this. That
ordering is what makes OCR evaluation possible at all: the ground truth is the
text the generator started from, so character error rate can be measured
exactly and for free, with no manual transcription.

Each effect corresponds to something real. Skew is a page fed crooked. Blur is a
sheet not flat on the platen. Speckle is a dirty scanner or a third-generation
photocopy. JPEG artefacts are a fax or an email attachment someone re-saved.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class ScanProfile:
    """How badly to damage a page."""

    name: str
    skew_degrees: float = 0.0
    blur_kernel: int = 0
    speckle: float = 0.0
    jpeg_quality: int = 95
    brightness: float = 1.0
    contrast: float = 1.0
    #: Simulates a book scanner leaving one edge darker than the other.
    gradient: float = 0.0
    target_dpi: int = 300


PROFILES: dict[str, ScanProfile] = {
    "clean": ScanProfile("clean", jpeg_quality=95, target_dpi=300),
    "office": ScanProfile(
        "office", skew_degrees=0.4, blur_kernel=3, speckle=0.004,
        jpeg_quality=80, target_dpi=200,
    ),
    "photocopy": ScanProfile(
        "photocopy", skew_degrees=1.2, blur_kernel=3, speckle=0.015,
        jpeg_quality=65, contrast=1.25, gradient=0.18, target_dpi=200,
    ),
    "fax": ScanProfile(
        "fax", skew_degrees=2.1, blur_kernel=5, speckle=0.03,
        jpeg_quality=45, brightness=0.9, contrast=1.4, gradient=0.25, target_dpi=150,
    ),
}


def degrade(image: np.ndarray, profile: ScanProfile, *, seed: int = 0) -> np.ndarray:
    """Apply a scan profile to a rendered page."""
    rng = random.Random(seed)
    working = image.copy()

    if profile.skew_degrees:
        # A small random component, because a real stack of scans is not all
        # crooked by exactly the same amount.
        angle = rng.uniform(-profile.skew_degrees, profile.skew_degrees)
        height, width = working.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        working = cv2.warpAffine(
            working, matrix, (width, height),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
        )

    if profile.brightness != 1.0 or profile.contrast != 1.0:
        working = cv2.convertScaleAbs(
            working, alpha=profile.contrast, beta=(profile.brightness - 1.0) * 255
        )

    if profile.gradient:
        height, width = working.shape[:2]
        ramp = np.linspace(1.0, 1.0 - profile.gradient, width, dtype=np.float32)
        mask = np.tile(ramp, (height, 1))
        if working.ndim == 3:
            mask = np.dstack([mask] * working.shape[2])
        working = np.clip(working.astype(np.float32) * mask, 0, 255).astype(np.uint8)

    if profile.blur_kernel > 1:
        k = profile.blur_kernel | 1  # must be odd
        working = cv2.GaussianBlur(working, (k, k), 0)

    if profile.speckle:
        noise = np.zeros(working.shape[:2], dtype=np.uint8)
        cv2.randu(noise, 0, 255)
        threshold = int(profile.speckle * 255)
        working[noise < threshold] = 0                      # pepper
        working[noise > (255 - threshold)] = 255            # salt

    if profile.jpeg_quality < 95:
        ok, buffer = cv2.imencode(
            ".jpg", working, [int(cv2.IMWRITE_JPEG_QUALITY), profile.jpeg_quality]
        )
        if ok:
            working = cv2.imdecode(buffer, cv2.IMREAD_COLOR)

    return working


def pdf_to_scanned_pdf(
    source: Path, target: Path, profile: ScanProfile, *, seed: int = 0
) -> int:
    """Render a native PDF to images, degrade them, and rebuild it as a scan.

    The result has no text layer at all, which is the point — it forces the
    ingestion pipeline down the OCR path exactly as a real scan would.
    """
    import pymupdf

    zoom = profile.target_dpi / 72.0
    output = pymupdf.open()

    with pymupdf.open(source) as pdf:
        for index, page in enumerate(pdf):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n == 4:
                array = cv2.cvtColor(array, cv2.COLOR_RGBA2BGR)
            elif pixmap.n == 3:
                array = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)

            damaged = degrade(array, profile, seed=seed + index)
            ok, encoded = cv2.imencode(".jpg", damaged, [int(cv2.IMWRITE_JPEG_QUALITY), profile.jpeg_quality])
            if not ok:
                continue

            new_page = output.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=bytes(encoded))

    target.parent.mkdir(parents=True, exist_ok=True)
    output.save(str(target))
    pages = output.page_count
    output.close()
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="photocopy")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    pages = pdf_to_scanned_pdf(args.source, args.target, PROFILES[args.profile], seed=args.seed)
    print(f"wrote {args.target} — {pages} page(s), profile '{args.profile}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
