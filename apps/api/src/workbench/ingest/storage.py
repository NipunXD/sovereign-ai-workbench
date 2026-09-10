"""Content-addressed blob storage.

Files are stored under the SHA-256 of their contents, fanned out two levels so
no directory ends up with a hundred thousand entries. Two consequences that
matter operationally:

* Re-uploading a document that already exists costs nothing and cannot produce
  a divergent second copy. Refinery documents circulate by email and get
  uploaded repeatedly.
* The stored path is itself the integrity check. A blob that no longer hashes to
  its own filename has been corrupted, and that is detectable without a separate
  manifest.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from workbench.core.logging import get_logger

log = get_logger(__name__)

#: Streaming chunk size. A 200 MB upload must never be held in memory whole.
READ_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class StoredBlob:
    sha256: str
    path: Path
    size_bytes: int
    #: False when an identical blob was already present.
    newly_written: bool


class BlobStore:
    """Immutable file storage keyed by content hash."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put_stream(self, stream: BinaryIO, *, max_bytes: int | None = None) -> StoredBlob:
        """Store from a stream, hashing as it is written.

        Written to a temporary file first and moved into place only once
        complete, so an interrupted upload cannot leave a truncated blob at a
        path that claims to be a full document.
        """
        import tempfile

        hasher = hashlib.sha256()
        size = 0
        temp_dir = self.root / "_incoming"
        temp_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as temp:
            temp_path = Path(temp.name)
            while chunk := stream.read(READ_CHUNK):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    temp_path.unlink(missing_ok=True)
                    from workbench.core.errors import PayloadTooLargeError

                    raise PayloadTooLargeError(f"upload exceeds the {max_bytes / 1e6:.0f} MB limit")
                hasher.update(chunk)
                temp.write(chunk)

        digest = hasher.hexdigest()
        target = self.path_for(digest)
        if target.is_file():
            temp_path.unlink(missing_ok=True)
            return StoredBlob(digest, target, target.stat().st_size, newly_written=False)

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), target)
        target.chmod(0o440)  # read-only: an ingested source is never edited
        return StoredBlob(digest, target, size, newly_written=True)

    def put_bytes(self, data: bytes) -> StoredBlob:
        import io

        return self.put_stream(io.BytesIO(data))

    def open(self, sha256: str) -> BinaryIO:
        path = self.path_for(sha256)
        if not path.is_file():
            from workbench.core.errors import NotFoundError

            raise NotFoundError(f"no stored blob {sha256[:12]}…")
        return path.open("rb")

    def verify(self, sha256: str) -> bool:
        """Re-hash a blob and confirm it still matches its own name."""
        path = self.path_for(sha256)
        if not path.is_file():
            return False
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(READ_CHUNK):
                hasher.update(chunk)
        return hasher.hexdigest() == sha256

    def delete(self, sha256: str) -> bool:
        """Remove a blob. Only for a hard delete; soft deletes keep the file."""
        path = self.path_for(sha256)
        if not path.is_file():
            return False
        path.chmod(0o640)
        path.unlink()
        return True
