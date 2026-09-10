"""The audit log, exposed for review.

Reading the audit log is itself a privileged action, and it is audited — an
auditor who goes looking leaves a trace like everyone else.

Note what is deliberately *not* here: document contents. The log records that a
restricted document was retrieved, by whom, and when, but never what it said.
Otherwise audit access would become a way around document classification, and
the person reviewing access would need the clearance of everything they review.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from workbench.api.deps import Audit, DbSession, require_permission
from workbench.core.clock import now
from workbench.core.hashing import canonical_json
from workbench.db.models import AuditEvent
from workbench.security.audit import verify_chain
from workbench.security.rbac import Principal

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    seq: int
    ts: str
    actor_username: str | None
    actor_roles: list[str]
    action: str
    decision: str
    reason: str | None
    resource_type: str | None
    resource_id: str | None
    run_id: str | None
    model_used: str | None
    lane: str | None
    tool_name: str | None
    latency_ms: int | None
    severity: int
    metadata: dict[str, Any]
    hash: str
    prev_hash: str


class AuditPage(BaseModel):
    events: list[AuditEventOut]
    total: int
    #: Distinct values present in the log, so the UI can offer real filters
    #: rather than a hardcoded list that drifts from what actually happens.
    actions: list[str]
    actors: list[str]


class ChainReportOut(BaseModel):
    valid: bool
    events_checked: int
    first_invalid_seq: int | None
    detail: str


def _serialise(event: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        seq=event.seq or 0,
        ts=event.ts.isoformat(),
        actor_username=event.actor_username,
        actor_roles=list(event.actor_roles),
        action=event.action,
        decision=event.decision,
        reason=event.reason,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        run_id=event.run_id,
        model_used=event.model_used,
        lane=event.lane,
        tool_name=event.tool_name,
        latency_ms=event.latency_ms,
        severity=event.severity,
        metadata=event.audit_metadata or {},
        hash=event.hash,
        prev_hash=event.prev_hash,
    )


@router.get("/events", response_model=AuditPage)
async def list_events(
    session: DbSession,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("audit:read"))],
    actor: str | None = None,
    action: str | None = None,
    decision: str | None = None,
    run_id: str | None = None,
    since_hours: int | None = Query(default=None, ge=1, le=8760),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    """Read the log, newest first."""
    # Annotated, and each field wrapped in col(): SQLModel declares these
    # fields as their Python types, so `AuditEvent.action == action` looks like
    # a bool to a type checker and like a SQL expression to SQLAlchemy. col()
    # says which one is meant.
    conditions: list[ColumnElement[bool]] = []
    if actor:
        conditions.append(col(AuditEvent.actor_username) == actor)
    if action:
        conditions.append(col(AuditEvent.action) == action)
    if decision:
        conditions.append(col(AuditEvent.decision) == decision)
    if run_id:
        conditions.append(col(AuditEvent.run_id) == run_id)
    if since_hours:
        conditions.append(col(AuditEvent.ts) >= now() - timedelta(hours=since_hours))

    query = select(AuditEvent)
    for condition in conditions:
        query = query.where(condition)

    total_query = select(func.count(col(AuditEvent.id)))
    for condition in conditions:
        total_query = total_query.where(condition)
    total = (await session.execute(total_query)).scalar_one()

    events = list(
        (
            await session.execute(
                query.order_by(col(AuditEvent.seq).desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )

    # Filter options come from the data, so they cannot drift from reality.
    actions = [
        row[0]
        for row in (
            await session.execute(
                select(col(AuditEvent.action)).distinct().order_by(col(AuditEvent.action))
            )
        ).all()
    ]
    actors = [
        row[0]
        for row in (
            await session.execute(
                select(col(AuditEvent.actor_username))
                .distinct()
                .where(col(AuditEvent.actor_username).is_not(None))
                .order_by(col(AuditEvent.actor_username))
            )
        ).all()
    ]

    await audit.log(
        "audit.read",
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        actor_roles=sorted(principal.roles),
        resource_type="audit_log",
        metadata={"filters": {"actor": actor, "action": action, "decision": decision}},
    )

    return AuditPage(
        events=[_serialise(event) for event in events],
        total=int(total),
        actions=actions,
        actors=actors,
    )


@router.get("/verify", response_model=ChainReportOut)
async def verify(
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("audit:read"))],
) -> ChainReportOut:
    """Recompute the hash chain and report the first break.

    Every record commits to the digest of its predecessor, so altering or
    removing any historical row invalidates every hash after it. This walks the
    chain and names the exact sequence number where that happens.
    """
    report = await verify_chain(session)
    return ChainReportOut(
        valid=report.valid,
        events_checked=report.events_checked,
        first_invalid_seq=report.first_invalid_seq,
        detail=report.detail,
    )


@router.get("/export")
async def export(
    session: DbSession,
    audit: Audit,
    principal: Annotated[Principal, Depends(require_permission("audit:read"))],
    since_hours: int | None = Query(default=None, ge=1, le=8760),
) -> StreamingResponse:
    """Stream the log as JSON lines, for offline verification.

    Each line carries its own hash and its predecessor's, so the chain can be
    checked by someone who does not have access to this system at all — which
    is the point of an evidence bundle.
    """
    query = select(AuditEvent).order_by(col(AuditEvent.seq))
    if since_hours:
        query = query.where(col(AuditEvent.ts) >= now() - timedelta(hours=since_hours))

    events = list((await session.execute(query)).scalars())

    await audit.log(
        "audit.export",
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        resource_type="audit_log",
        metadata={"events": len(events)},
    )

    async def lines() -> Any:
        for event in events:
            payload = {**event.chain_payload(), "hash": event.hash, "prev_hash": event.prev_hash}
            yield canonical_json(payload) + "\n"

    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.jsonl"'},
    )


@router.get("/summary")
async def summary(
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("audit:read"))],
    since_hours: int = Query(default=24, ge=1, le=8760),
) -> dict[str, Any]:
    """Counts by action and decision, for the review dashboard."""
    since = now() - timedelta(hours=since_hours)

    by_action = [
        {"action": row[0], "count": int(row[1])}
        for row in (
            await session.execute(
                select(col(AuditEvent.action), func.count(col(AuditEvent.id)))
                .where(col(AuditEvent.ts) >= since)
                .group_by(col(AuditEvent.action))
                .order_by(func.count(col(AuditEvent.id)).desc())
            )
        ).all()
    ]
    by_decision = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(col(AuditEvent.decision), func.count(col(AuditEvent.id)))
                .where(col(AuditEvent.ts) >= since)
                .group_by(col(AuditEvent.decision))
            )
        ).all()
    }
    # Denials are what a reviewer looks for first, so they are surfaced rather
    # than left to be found by filtering.
    denials = list(
        (
            await session.execute(
                select(AuditEvent)
                .where(col(AuditEvent.decision) == "deny", col(AuditEvent.ts) >= since)
                .order_by(col(AuditEvent.seq).desc())
                .limit(10)
            )
        ).scalars()
    )

    return {
        "since_hours": since_hours,
        "by_action": by_action,
        "by_decision": by_decision,
        "recent_denials": [_serialise(event).model_dump() for event in denials],
    }
