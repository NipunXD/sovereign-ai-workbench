"""Image preprocessing for OCR.

Scanned refinery paperwork is photocopied, faxed, stapled and re-scanned. The
recogniser sees skew, speckle, uneven illumination from a book scanner, and
small type. Each step here targets one of those, and each is optional because
applying them to an already-clean render makes results worse, not better.

Order matters: deskew before thresholding (rotating a binary image leaves
stair-stepped edges), and denoise before CLAHE (contrast enhancement amplifies
speckle otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from workbench.core.logging import get_logger

log = get_logger(__name__)

#: Beyond this the detected angle is almost certainly a misdetection — a page
#: rotated 40 degrees is a scanning accident, not skew, and "correcting" it
#: would destroy a page that was merely unusual.
MAX_DESKEW_DEGREES = 15.0


@dataclass(frozen=True, slots=True)
class PreprocessOptions:
    """Defaults chosen by measurement, not by tradition.

    Measured on the seed corpus (character error rate, mean of the photocopy and
    office scan profiles):

        raw                     16.6%
        CLAHE only               7.6%   <- default
        CLAHE + denoise          8.7%
        CLAHE + threshold       67.5%

    Binarisation is a Tesseract-era step and it is actively destructive here:
    RapidOCR's detection and recognition models are trained on natural greyscale
    images, and thresholding throws away the antialiasing they depend on. Median
    denoise is a smaller version of the same mistake — it softens strokes that
    are already thin on a 200 dpi scan.

    Both remain available because a different engine has different preferences;
    Tesseract genuinely does better on binarised input.
    """

    deskew: bool = True
    denoise: bool = False
    clahe: bool = True
    adaptive_threshold: bool = False
    min_effective_dpi: int = 300
    source_dpi: int = 300


@dataclass(frozen=True, slots=True)
class PreprocessResult:
    image: np.ndarray
    deskew_angle: float = 0.0
    upscaled: float = 1.0
    steps: tuple[str, ...] = ()


def preprocess(image: np.ndarray, options: PreprocessOptions | None = None) -> PreprocessResult:
    """Prepare a page image for recognition."""
    options = options or PreprocessOptions()
    steps: list[str] = []

    working = image
    if working.ndim == 3:
        working = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
        steps.append("grayscale")

    scale = 1.0
    if options.source_dpi < options.min_effective_dpi:
        # Small type below ~300 dpi loses the strokes that distinguish 8 from B.
        scale = options.min_effective_dpi / options.source_dpi
        working = cv2.resize(working, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        steps.append(f"upscale x{scale:.2f}")

    angle = 0.0
    if options.deskew:
        angle = estimate_skew(working)
        if abs(angle) > 0.15:
            working = rotate(working, angle)
            steps.append(f"deskew {angle:+.2f}deg")

    if options.denoise:
        # Median blur removes scanner speckle without softening stroke edges the
        # way a Gaussian would.
        working = cv2.medianBlur(working, 3)
        steps.append("denoise")

    if options.clahe:
        # Local rather than global equalisation: a book-scanner gradient leaves
        # one side of the page dark, which a global histogram cannot fix.
        working = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(working)
        steps.append("clahe")

    if options.adaptive_threshold:
        working = cv2.adaptiveThreshold(
            working, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
        )
        steps.append("adaptive threshold")

    return PreprocessResult(
        image=working, deskew_angle=angle, upscaled=scale, steps=tuple(steps)
    )


def normalise_angle(angle: float) -> float:
    """Fold a rectangle angle into the [-45, 45] correction it implies.

    ``cv2.minAreaRect`` has reported its angle in two different ranges across
    OpenCV versions — ``(0, 90]`` in 4.5+ and ``[-90, 0)`` in 5.x — and a
    square-ish text mask can land at either end. Folding by 90 handles both,
    which matters because getting it wrong does not error: deskew silently
    stops running and every scan is recognised crooked.
    """
    while angle < -45.0:
        angle += 90.0
    while angle > 45.0:
        angle -= 90.0
    return angle


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate the rotation needed to straighten a page, in degrees.

    Uses the minimum-area rectangle around the text mask, which is robust on a
    dense page of prose. Returns 0 when the estimate is implausible rather than
    rotating a page based on a bad measurement.
    """
    try:
        inverted = cv2.bitwise_not(gray)
        _, mask = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        coords = cv2.findNonZero(mask)
        if coords is None or len(coords) < 50:
            return 0.0

        angle = normalise_angle(float(cv2.minAreaRect(coords)[-1]))
        if abs(angle) > MAX_DESKEW_DEGREES:
            log.debug("skew_estimate_rejected", angle=angle)
            return 0.0
        return angle
    except cv2.error as exc:
        log.debug("skew_estimate_failed", error=str(exc))
        return 0.0


def rotate(image: np.ndarray, angle: float) -> np.ndarray:
    """Rotate about the centre, expanding the canvas so nothing is clipped."""
    height, width = image.shape[:2]
    centre = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)

    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sin + width * cos)
    new_height = int(height * cos + width * sin)
    matrix[0, 2] += new_width / 2 - centre[0]
    matrix[1, 2] += new_height / 2 - centre[1]

    return cv2.warpAffine(
        image, matrix, (new_width, new_height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def decode(data: bytes) -> np.ndarray:
    """Decode image bytes into an OpenCV array."""
    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        from workbench.core.errors import IngestionError

        raise IngestionError("the image could not be decoded")
    return image
