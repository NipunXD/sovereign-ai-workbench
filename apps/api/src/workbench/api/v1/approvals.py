"""Approvals and artifacts.

The approver is a different person, at a different browser, minutes later. So
the queue is a real endpoint rather than a callback, and a decision is durable
before anything acts on it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from workbench.api.deps import Audit, CurrentPrincipal, DbSession, require_permission
from workbench.core.errors import NotFoundError
from workbench.db.models import (
    Approval,
    Artifact,
    ArtifactStatus,
    User,
)
from workbench.security.rbac import Principal

router = APIRouter(tags=["approvals"])


class ApprovalOut(BaseModel):
    id: str
    run_id: str | None
    kind: str
    subject_type: str
    subject_id: str
    requested_by: str | None
    requested_by_name: str | None
    requested_at: str
    payload_summary: dict[str, Any]
    status: str
    decided_by: str | None
    decided_at: str | None
    comment: str | None
    expires_at: str | None
    #: True when this principal may not decide it — they asked for it.
    is_own_request: bool = False


class DecisionRequest(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)


class ArtifactOut(BaseModel):
    id: str
    run_id: str | None
    kind: str
    filename: str
    mime: str
    sha256: str
    size_bytes: int
    status: str
    version: int
    created_by: str | None
    created_at: str
    provenance: dict[str, Any]


async def _serialise(session: DbSession, approval: Approval, principal: Principal) -> ApprovalOut:
    name = None
    if approval.requested_by:
        user = (
            await session.execute(select(User).where(User.id == approval.requested_by))
        ).scalar_one_or_none()
        name = user.full_name or user.username if user else None
    return ApprovalOut(
        id=approval.id,
        run_id=approval.run_id,
        kind=approval.kind,
        subject_type=approval.subject_type,
        subject_id=approval.subject_id,
        requested_by=approval.requested_by,
        requested_by_name=name,
        requested_at=approval.requested_at.isoformat(),
        payload_summary=approval.payload_summary or {},
        status=approval.status,
        decided_by=approval.decided_by,
        decided_at=approval.decided_at.isoformat() if approval.decided_at else None,
        comment=approval.comment,
        expires_at=approval.expires_at.isoformat() if approval.expires_at else None,
        is_own_request=approval.requested_by == principal.user_id,
    )


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    session: DbSession,
    principal: CurrentPrincipal,
    status: str = "pending",
    limit: int = 50,
) -> list[ApprovalOut]:
    """The approval queue.

    Visible to anyone who can approve *or* who raised a request — an engineer
    needs to see that their report is waiting on someone.
    """
    query = select(Approval).order_by(Approval.requested_at.desc()).limit(limit)
    if status != "all":
        query = query.where(Approval.status == status)
    if not principal.has("artifact:approve") and not principal.has("run:approve"):
        query = query.where(Approval.requested_by == principal.user_id)

    approvals = list((await session.execute(query)).scalars())
    return [await _serialise(session, approval, principal) for approval in approvals]


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
async def decide(
    approval_id: str,
    payload: DecisionRequest,
    request: Request,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("artifact:approve"))],
) -> ApprovalOut:
    """Approve or reject.

    The gate refuses a self-approval regardless of permissions: approval means a
    second person looked, and one person holding both roles does not change
    that.
    """
    gate = request.app.state.approval_gate
    approval = await gate.decide(
        approval_id, approver=principal, approved=payload.approved, comment=payload.comment
    )

    # An artifact's status follows its approval, so a rejected report cannot be
    # downloaded as though it had been signed off.
    if approval.subject_type == "artifact":
        artifact = (
            await session.execute(select(Artifact).where(Artifact.sha256 == approval.subject_id))
        ).scalar_one_or_none()
        if artifact is not None:
            artifact.status = (
                ArtifactStatus.APPROVED if payload.approved else ArtifactStatus.REJECTED
            )
            provenance = dict(artifact.provenance or {})
            provenance["approved_by"] = principal.username
            provenance["approved_at"] = (
                approval.decided_at.isoformat() if approval.decided_at else None
            )
            artifact.provenance = provenance
            await session.flush()

    return await _serialise(session, approval, principal)


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    session: DbSession,
    principal: CurrentPrincipal,
    status: str | None = None,
    limit: int = 50,
) -> list[ArtifactOut]:
    query = select(Artifact).order_by(Artifact.created_at.desc()).limit(limit)
    if status:
        query = query.where(Artifact.status == status)
    artifacts = list((await session.execute(query)).scalars())
    return [
        ArtifactOut(
            id=a.id,
            run_id=a.run_id,
            kind=a.kind,
            filename=a.filename,
            mime=a.mime,
            sha256=a.sha256,
            size_bytes=a.size_bytes,
            status=a.status,
            version=a.version,
            created_by=a.created_by,
            created_at=a.created_at.isoformat(),
            provenance=a.provenance or {},
        )
        for a in artifacts
    ]


@router.get("/artifacts/{sha256}/download")
async def download_artifact(
    sha256: str,
    request: Request,
    session: DbSession,
    audit: Audit,
    principal: CurrentPrincipal,
) -> Response:
    """Download a generated file.

    A draft awaiting approval is downloadable only by the person who asked for
    it. Otherwise "pending approval" would be advisory: anyone could take the
    unapproved report and send it on.
    """
    artifact = (
        await session.execute(select(Artifact).where(Artifact.sha256 == sha256))
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFoundError("No such artifact.")

    if artifact.status in (ArtifactStatus.DRAFT, ArtifactStatus.PENDING_APPROVAL):
        may_see = artifact.created_by == principal.user_id or principal.has("artifact:approve")
        if not may_see:
            await audit.deny(
                "artifact.download",
                reason="artifact is awaiting approval",
                actor_user_id=principal.user_id,
                actor_username=principal.username,
                resource_type="artifact",
                resource_id=sha256,
            )
            raise NotFoundError("No such artifact.")

    if artifact.status == ArtifactStatus.REJECTED and not principal.has("artifact:approve"):
        raise NotFoundError("No such artifact.")

    store = request.app.state.artifact_store
    data = store.read(sha256)

    await audit.log(
        "artifact.download",
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        resource_type="artifact",
        resource_id=sha256,
        metadata={"filename": artifact.filename, "status": artifact.status},
    )

    return Response(
        content=data,
        media_type=artifact.mime,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )
