"""File type detection from content signatures.

Deliberately implemented against magic bytes rather than ``python-magic``,
which binds to the system ``libmagic``. That library is one more thing to
install on an air-gapped rack, one more thing that can be the wrong version,
and it is unnecessary for the closed set of formats this system accepts.

Detection is content-first: the extension a user typed is a hint, not evidence.
A ``.pdf`` that is really a ZIP must be rejected, not parsed as a PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Formats the pipeline can actually process. Anything else is refused at
#: upload rather than failing deep inside an extractor.
ALLOWED_MIME: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/plain",
        "message/rfc822",
    }
)

#: (offset, signature, mime). Order matters: more specific first.
_SIGNATURES: Final[tuple[tuple[int, bytes, str], ...]] = (
    (0, b"%PDF-", "application/pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"II*\x00", "image/tiff"),
    (0, b"MM\x00*", "image/tiff"),
    (0, b"BM", "image/bmp"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (8, b"WEBP", "image/webp"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),  # legacy Office
)

#: OOXML files are ZIP archives; the specific format is decided by what is
#: inside, so the ZIP signature alone cannot distinguish docx from xlsx.
_ZIP_SIGNATURES: Final[tuple[bytes, ...]] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

_OOXML_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
)

_EXTENSION_HINTS: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".eml": "message/rfc822",
}


@dataclass(frozen=True, slots=True)
class SniffResult:
    mime: str
    #: True when the declared extension disagrees with the content. Not fatal on
    #: its own — a mislabelled file is usually a mistake, not an attack — but it
    #: is recorded on the document and shown in the UI.
    extension_mismatch: bool = False
    detail: str = ""

    @property
    def allowed(self) -> bool:
        return self.mime in ALLOWED_MIME


def sniff(data: bytes, filename: str | None = None) -> SniffResult:
    """Identify a file from its leading bytes.

    Only the first few kilobytes are needed, so callers may pass a prefix rather
    than a whole 200 MB upload.
    """
    detected = _detect(data)
    hinted = _EXTENSION_HINTS.get(Path(filename).suffix.lower()) if filename else None

    if detected is None:
        # No signature matched. Text has no magic bytes, so fall back to a
        # decodability check before giving up.
        if _looks_like_text(data):
            detected = hinted if hinted in {"text/csv", "message/rfc822"} else "text/plain"
        else:
            return SniffResult(
                mime="application/octet-stream",
                detail="no recognised file signature",
            )

    mismatch = bool(hinted and hinted != detected)
    return SniffResult(
        mime=detected,
        extension_mismatch=mismatch,
        detail=(
            f"content is {detected} but the filename suggests {hinted}"
            if mismatch
            else ""
        ),
    )


def _detect(data: bytes) -> str | None:
    for offset, signature, mime in _SIGNATURES:
        if data[offset : offset + len(signature)] == signature:
            return mime
    if any(data.startswith(sig) for sig in _ZIP_SIGNATURES):
        return _detect_ooxml(data)
    return None


def _detect_ooxml(data: bytes) -> str:
    """Distinguish docx/pptx/xlsx by reading the archive's central directory."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        # A truncated upload: the prefix is a valid ZIP header but the central
        # directory is not present yet. Fall back to scanning the raw bytes.
        for marker, mime in _OOXML_MARKERS:
            if marker.encode() in data:
                return mime
        return "application/zip"

    for marker, mime in _OOXML_MARKERS:
        if marker in names:
            return mime
    return "application/zip"


def _looks_like_text(data: bytes, sample: int = 4096) -> bool:
    """Whether the bytes decode as UTF-8 and lack control characters."""
    chunk = data[:sample]
    if not chunk:
        return False
    if b"\x00" in chunk:
        return False
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # Tabs, newlines and carriage returns are the only control codes expected
    # in a document; anything else suggests binary content.
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
    return control / len(text) < 0.01
