"""Artifact storage.

Content-addressed like uploaded documents, for the same reason: regenerating a
report from identical inputs must not accumulate copies, and the stored path is
its own integrity check.

Files are written read-only. An approved artifact is a record of what was
approved; if it can be edited in place afterwards, the approval means nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workbench.artifacts.base import ArtifactBytes
from workbench.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    sha256: str
    path: Path
    size_bytes: int
    filename: str
    mime: str
    newly_written: bool


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def put(self, artifact: ArtifactBytes) -> StoredArtifact:
        target = self.path_for(artifact.sha256)
        if target.is_file():
            return StoredArtifact(
                sha256=artifact.sha256,
                path=target,
                size_bytes=target.stat().st_size,
                filename=artifact.filename,
                mime=artifact.mime,
                newly_written=False,
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact.data)
        target.chmod(0o440)
        return StoredArtifact(
            sha256=artifact.sha256,
            path=target,
            size_bytes=artifact.size_bytes,
            filename=artifact.filename,
            mime=artifact.mime,
            newly_written=True,
        )

    def read(self, sha256: str) -> bytes:
        path = self.path_for(sha256)
        if not path.is_file():
            from workbench.core.errors import NotFoundError

            raise NotFoundError("That artifact is no longer in storage.")
        return path.read_bytes()

    def verify(self, sha256: str) -> bool:
        from workbench.core.hashing import digest_file

        path = self.path_for(sha256)
        return path.is_file() and digest_file(path) == sha256
