"""The artifact contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from workbench.artifacts.provenance import Provenance


class ArtifactKind(StrEnum):
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    PDF = "pdf"
    PNG = "png"
    CSV = "csv"


MIME_TYPES: dict[ArtifactKind, str] = {
    ArtifactKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ArtifactKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ArtifactKind.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ArtifactKind.PDF: "application/pdf",
    ArtifactKind.PNG: "image/png",
    ArtifactKind.CSV: "text/csv",
}


@dataclass
class ArtifactBytes:
    """A rendered artifact, before it is stored."""

    data: bytes
    kind: ArtifactKind
    filename: str
    sha256: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def mime(self) -> str:
        return MIME_TYPES[self.kind]

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@runtime_checkable
class ArtifactBuilder(Protocol):
    kind: ArtifactKind

    def build(self, spec: Any, provenance: Provenance) -> ArtifactBytes: ...
