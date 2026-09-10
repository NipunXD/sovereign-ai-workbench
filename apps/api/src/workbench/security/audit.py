"""Audit logging and chain verification.

Writes are append-only and hash-chained: each row commits to the digest of its
predecessor, so altering or removing any historical row invalidates every hash
after it. ``verify_chain`` reports the exact sequence number where that happens.

The chain is only meaningful if rows are appended in a single, agreed order, so
``log`` serialises writers behind a lock and takes the previous hash under the
same transaction. Under Postgres this is backed further by a trigger that
rejects UPDATE and DELETE on the table outright (see the initial migration).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from workbench.core.clock import now
from workbench.core.hashing import GENESIS_HASH, chain_hash
from workbench.core.logging import get_logger
from workbench.core.redaction import redact
from workbench.db.models.audit import AuditDecision, AuditEvent, Severity

log = get_logger(__name__)

#: Serialises appends within a process so the chain has one definite order.
_chain_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class ChainReport:
    """Result of walking the audit chain."""

    valid: bool
    events_checked: int
    first_invalid_seq: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "events_checked": self.events_checked,
            "first_invalid_seq": self.first_invalid_seq,
            "detail": self.detail,
        }


class AuditLogger:
    """Appends to the audit chain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _previous_hash(self) -> str:
        result = await self.session.execute(
            select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
        )
        return result.scalar_one_or_none() or GENESIS_HASH

    async def log(
        self,
        action: str,
        *,
        decision: str = AuditDecision.ALLOW,
        actor_user_id: str | None = None,
        actor_username: str | None = None,
        actor_roles: list[str] | None = None,
        actor_ip: str | None = None,
        user_agent: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        request_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        reason: str | None = None,
        model_used: str | None = None,
        lane: str | None = None,
        tool_name: str | None = None,
        input_digest: str | None = None,
        output_digest: str | None = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        severity: int = Severity.INFO,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append one event.

        Metadata is redacted before persistence: the audit log records that a
        restricted document was retrieved, never what it said. That keeps audit
        access from becoming a way around document classification.
        """
        async with _chain_lock:
            # Reserve the sequence number inside the same transaction that reads
            # the previous hash, so two concurrent writers cannot interleave.
            seq = (
                await self.session.execute(text("SELECT nextval('audit_events_seq_seq')"))
            ).scalar_one()

            event = AuditEvent(
                seq=int(seq),
                ts=now(),
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                actor_roles=actor_roles or [],
                actor_ip=actor_ip,
                user_agent=user_agent,
                session_id=session_id,
                conversation_id=conversation_id,
                run_id=run_id,
                step_id=step_id,
                request_id=request_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                decision=decision,
                reason=reason,
                model_used=model_used,
                lane=lane,
                tool_name=tool_name,
                input_digest=input_digest,
                output_digest=output_digest,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                severity=severity,
                audit_metadata=redact(metadata or {}),
            )
            event.prev_hash = await self._previous_hash()
            event.hash = chain_hash(event.chain_payload(), event.prev_hash)

            self.session.add(event)
            await self.session.flush()

        if decision == AuditDecision.DENY:
            # Denials are the events a reviewer looks for first; surface them in
            # the operational logs too, not only in the database.
            log.warning(
                "access_denied",
                action=action,
                actor=actor_username,
                resource=f"{resource_type}:{resource_id}",
                reason=reason,
            )
        return event

    async def deny(self, action: str, *, reason: str, **kwargs: Any) -> AuditEvent:
        """Record a refused action."""
        kwargs.pop("decision", None)
        kwargs.pop("severity", None)
        return await self.log(
            action,
            decision=AuditDecision.DENY,
            reason=reason,
            severity=Severity.WARNING,
            **kwargs,
        )


async def verify_chain(
    session: AsyncSession, *, start_seq: int = 0, limit: int | None = None
) -> ChainReport:
    """Recompute every hash and report the first break.

    A tampered row is detected at its own sequence number, and every subsequent
    row is invalid as a consequence — which is the property that makes the log
    evidence rather than merely a record.
    """
    query = select(AuditEvent).where(AuditEvent.seq > start_seq).order_by(AuditEvent.seq)
    if limit:
        query = query.limit(limit)

    events = list((await session.execute(query)).scalars())
    if not events:
        return ChainReport(valid=True, events_checked=0, detail="no events to verify")

    expected_prev = GENESIS_HASH
    if start_seq > 0:
        prior = (
            await session.execute(
                select(AuditEvent.hash)
                .where(AuditEvent.seq <= start_seq)
                .order_by(AuditEvent.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        expected_prev = prior or GENESIS_HASH

    for index, event in enumerate(events):
        if event.prev_hash != expected_prev:
            return ChainReport(
                valid=False,
                events_checked=index,
                first_invalid_seq=event.seq,
                detail=(
                    f"event {event.seq} claims predecessor {event.prev_hash[:12]}… "
                    f"but the chain expects {expected_prev[:12]}… — a record was "
                    f"altered, deleted or inserted at or before this point"
                ),
            )
        recomputed = chain_hash(event.chain_payload(), event.prev_hash)
        if recomputed != event.hash:
            return ChainReport(
                valid=False,
                events_checked=index,
                first_invalid_seq=event.seq,
                detail=(
                    f"event {event.seq} does not hash to its stored digest — "
                    f"its contents were modified after it was written"
                ),
            )
        expected_prev = event.hash

    return ChainReport(
        valid=True,
        events_checked=len(events),
        detail=f"verified {len(events)} events from seq {events[0].seq} to {events[-1].seq}",
    )
