"""Recording a generated artifact in the database.

Separate from the store, which holds the bytes. A file on disk with no row is
invisible to the approval queue and cannot be downloaded — it exists and
nobody can reach it, which is worse than not producing it, because the run
reports success.

Two callers write these rows and they sit in different layers: the chat stream
as each artifact is created, and a deferred approval carrying out an action
after the requesting run has gone. Hence a home below both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlmodel import col

from workbench.core.logging import get_logger
from workbench.db.models import Artifact, ArtifactStatus
from workbench.db.session import session_scope

log = get_logger(__name__)


async def persist_artifact(
    payload: dict[str, Any],
    *,
    run_id: str,
    conversation_id: str,
    created_by: str,
    approved: bool = False,
) -> None:
    """Record a generated artifact so it outlives whatever produced it.

    Written on its own session: the request transaction may still be
    streaming, and the deferred path may be running with no request at all.
    Failures are logged rather than raised — the bytes are already stored, and
    taking the caller down on the way to saying so helps nobody.
    """
    sha256 = str(payload.get("sha256") or "")
    if not sha256:
        return

    try:
        async with session_scope() as session:
            existing = (
                await session.execute(select(Artifact).where(col(Artifact.sha256) == sha256))
            ).scalar_one_or_none()
            if existing is not None:
                return
            session.add(
                Artifact(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    created_by=created_by,
                    kind=str(payload.get("kind", "")),
                    filename=str(payload.get("filename", "")),
                    mime=str(payload.get("mime", "")),
                    sha256=sha256,
                    size_bytes=int(payload.get("size_bytes", 0)),
                    storage_path="",
                    provenance=dict(payload.get("provenance") or {}),
                    status=(
                        ArtifactStatus.APPROVED if approved else ArtifactStatus.PENDING_APPROVAL
                    ),
                )
            )
    except Exception as exc:
        log.error("artifact_persist_failed", sha256=sha256[:12], error=str(exc))
