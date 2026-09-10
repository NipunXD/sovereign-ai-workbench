"""The human approval gate.

An agent that can generate a signed-looking inspection report and execute code
needs a point where a person says yes. The gate exists at two places: before a
tool whose spec requires approval, and before delivering a run that produced a
durable artifact or an engineering calculation.

The design constraint that shapes everything here is that a run must *always*
terminate. A gate that can wait forever is a queue of half-finished work nobody
can see, so every request carries an expiry and an expired request resolves as
a rejection rather than hanging.

Approval state lives in the database rather than in memory: the approver is a
different person at a different browser, often minutes later, and possibly after
the API has restarted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from workbench.core.clock import now
from workbench.core.hashing import digest
from workbench.core.logging import get_logger
from workbench.db.models import Approval, ApprovalStatus, AuditAction
from workbench.security.audit import AuditLogger
from workbench.security.rbac import Principal

log = get_logger(__name__)

#: How long a request waits before it is treated as refused.
DEFAULT_EXPIRY_HOURS = 24


@dataclass
class ApprovalRequest:
    kind: str  # "tool" | "artifact" | "final"
    subject_type: str
    subject_id: str
    summary: dict[str, Any]
    reason: str = ""


class ApprovalGate:
    """Creates and resolves approval requests."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        expiry_hours: int = DEFAULT_EXPIRY_HOURS,
    ) -> None:
        self.session_factory = session_factory
        self.expiry_hours = expiry_hours

    async def request(
        self,
        *,
        run_id: str,
        step_id: str | None,
        principal: Principal,
        request: ApprovalRequest,
    ) -> Approval:
        """Record a pending decision and return it."""
        async with self.session_factory() as session:
            approval = Approval(
                run_id=run_id,
                step_id=step_id,
                kind=request.kind,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                requested_by=principal.user_id,
                payload_summary=request.summary,
                payload_digest=digest(request.summary),
                status=ApprovalStatus.PENDING,
                expires_at=now() + timedelta(hours=self.expiry_hours),
            )
            session.add(approval)
            await session.flush()

            await AuditLogger(session).log(
                AuditAction.APPROVAL_REQUEST,
                actor_user_id=principal.user_id,
                actor_username=principal.username,
                actor_roles=sorted(principal.roles),
                run_id=run_id,
                step_id=step_id,
                resource_type="approval",
                resource_id=approval.id,
                reason=request.reason,
                metadata={"kind": request.kind, "subject": request.subject_id},
            )
            await session.commit()
            approval_id = approval.id

        log.info("approval_requested", approval_id=approval_id, run_id=run_id, kind=request.kind)
        return approval

    async def decide(
        self,
        approval_id: str,
        *,
        approver: Principal,
        approved: bool,
        comment: str = "",
    ) -> Approval:
        """Record a decision.

        Separation of duties is enforced here, not merely by which button the UI
        shows: the person who asked for something cannot be the person who
        approves it, whatever permissions they hold.
        """
        from workbench.core.errors import AuthorizationError, ConflictError, NotFoundError

        async with self.session_factory() as session:
            approval = (
                await session.execute(select(Approval).where(col(Approval.id) == approval_id))
            ).scalar_one_or_none()
            if approval is None:
                raise NotFoundError("No such approval request.")

            if approval.status is not None and approval.status != ApprovalStatus.PENDING:
                raise ConflictError(
                    f"This request was already {approval.status} and cannot be decided again."
                )

            if approval.requested_by == approver.user_id:
                raise AuthorizationError(
                    "You cannot approve your own request. Approval requires a second person."
                )

            if approval.expires_at and approval.expires_at < now():
                approval.status = ApprovalStatus.EXPIRED
                await session.commit()
                raise ConflictError(
                    "This request expired before it was decided, and was treated as refused."
                )

            approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            approval.decided_by = approver.user_id
            approval.decided_at = now()
            approval.comment = comment

            await AuditLogger(session).log(
                AuditAction.APPROVAL_DECIDE,
                actor_user_id=approver.user_id,
                actor_username=approver.username,
                actor_roles=sorted(approver.roles),
                run_id=approval.run_id,
                resource_type="approval",
                resource_id=approval.id,
                decision="allow" if approved else "deny",
                reason=comment or ("approved" if approved else "rejected"),
                metadata={"kind": approval.kind, "subject": approval.subject_id},
            )
            await session.commit()
            await session.refresh(approval)
            return approval

    async def status(self, approval_id: str) -> Approval | None:
        async with self.session_factory() as session:
            return (
                await session.execute(select(Approval).where(col(Approval.id) == approval_id))
            ).scalar_one_or_none()

    async def expire_stale(self) -> int:
        """Resolve requests nobody decided.

        Without this a run waits indefinitely on a decision that will never
        come. Expiry resolves it as a refusal, which is the safe direction.
        """
        async with self.session_factory() as session:
            stale = list(
                (
                    await session.execute(
                        select(Approval).where(
                            col(Approval.status) == ApprovalStatus.PENDING,
                            col(Approval.expires_at) < now(),
                        )
                    )
                ).scalars()
            )
            for approval in stale:
                approval.status = ApprovalStatus.EXPIRED
            if stale:
                await session.commit()
                log.info("approvals_expired", count=len(stale))
            return len(stale)
