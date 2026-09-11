"""Persisting the lifecycle of an agent run.

The `agent_runs` table existed from the first migration and nothing ever wrote
to it. That was invisible while runs completed — the trace streams live, so
nobody missed a record of it — and then a stream was cancelled two minutes in
and there was no way to answer the only question that mattered: is it still
working, or is it dead? The browser showed a spinner, the run had stopped, and
nothing in the system knew either way.

So a run is now recorded when it starts and finalised when it ends, *including*
when it ends because the client went away.

That last case is why the finalisation runs on its own connection. The request
session is being torn down by the same cancellation being recorded — closing
the record with it produced a `CancelledError` inside the teardown and lost the
write, which is exactly how the run that prompted this ended up with a
``run.start`` and no ``run.finish``. The pattern matches the one used for
failed-login auditing: a fact about a failure must not be stored in the
transaction that is failing.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import col

from workbench.core.clock import now
from workbench.core.logging import get_logger
from workbench.db.models import AgentRun, Conversation, RunStatus
from workbench.db.session import session_scope
from workbench.security.rbac import Principal

log = get_logger(__name__)

#: wall_ms is a 32-bit column. A real duration cannot approach this, but a
#: reaped orphan's arithmetic can, and overflowing the column loses the write.
_MAX_WALL_MS = 2_147_483_647


async def open_run(
    *,
    run_id: str,
    conversation_id: str,
    principal: Principal,
    message: str,
) -> None:
    """Record a run as started, creating its conversation if it is new.

    Best-effort: a run that cannot be recorded still runs. Losing the audit of
    a working request would be the wrong trade — the audit log proper is
    written separately and is the compliance record.
    """
    try:
        async with session_scope() as session:
            existing = await session.get(Conversation, conversation_id)
            if existing is None:
                session.add(
                    Conversation(
                        id=conversation_id,
                        user_id=principal.user_id,
                        title=message[:80] or "New conversation",
                    )
                )
                # Flushed before the run is added. AgentRun.conversation_id is
                # a foreign key, but no ORM relationship links the two classes,
                # so SQLAlchemy has no dependency to sort the INSERTs by and
                # will happily write the run first. That fails the constraint,
                # and because this helper is best-effort the failure is
                # swallowed — every run would simply go unrecorded.
                await session.flush()
            else:
                existing.updated_at = now()
            session.add(
                AgentRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    user_id=principal.user_id,
                    thread_id=run_id,
                    status=RunStatus.RUNNING,
                    input=message,
                )
            )
    except Exception as exc:
        log.error("run_open_failed", run_id=run_id, error=str(exc))


async def close_run(
    *,
    run_id: str,
    status: str,
    plan: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> None:
    """Finalise a run on a connection of its own.

    Called from a `finally`, so it may be running inside a cancelled task. It
    opens a fresh session for exactly that reason, and swallows its own
    failures: a run that has already ended cannot be made worse by failing to
    say so, but it can be made worse by raising out of a cleanup path.
    """
    try:
        async with session_scope() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = now()
            run.wall_ms = min(
                int((run.finished_at - run.started_at).total_seconds() * 1000), _MAX_WALL_MS
            )
            if plan is not None:
                run.plan = plan
            if budget is not None:
                run.budget = budget
            if validation is not None:
                run.validation = validation
            if error is not None:
                run.error = error
            if trace is not None:
                run.trace = trace
    except Exception as exc:
        log.error("run_close_failed", run_id=run_id, status=status, error=str(exc))


async def reap_orphans(older_than_s: float = 900.0) -> int:
    """Mark runs that were left running by a process that went away.

    A run only ends when its own request ends, so a killed or restarted API
    leaves rows stuck in `running` forever with nothing left alive to close
    them. Called at startup.

    The age window is not caution about this process — a starting process has
    no runs of its own in flight. It is about the others: the runs table is
    shared, and a second instance or a worker may have a genuinely live run at
    the moment this one boots. Reaping on age alone would mark that run dead
    while it was still streaming to somebody. Fifteen minutes is comfortably
    past the longest budget a run can be given, so a run older than that is
    not being served by anyone.
    """
    from sqlalchemy import select

    try:
        async with session_scope() as session:
            cutoff = now().timestamp() - older_than_s
            stale = list(
                (
                    await session.execute(
                        select(AgentRun).where(col(AgentRun.status) == RunStatus.RUNNING)
                    )
                ).scalars()
            )
            reaped = 0
            for run in stale:
                if run.started_at.timestamp() > cutoff:
                    continue
                run.status = RunStatus.CANCELLED
                run.finished_at = now()
                # wall_ms is deliberately left unset. The run ended when the
                # process did, and nothing recorded when that was — measuring
                # to the moment someone happened to restart would report a
                # duration that never happened.
                run.error = {"reason": "the process serving this run exited"}
                reaped += 1
            if reaped:
                log.info("stale_runs_reaped", count=reaped)
            return reaped
    except Exception as exc:
        log.error("run_reap_failed", error=str(exc))
        return 0


async def persist_message(
    *,
    conversation_id: str,
    role: str,
    content: str,
    run_id: str | None = None,
    model_used: str | None = None,
    latency_ms: int | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> None:
    """Write one turn of a conversation, with its resolved citations.

    Best-effort, on its own session, for the same reasons as :func:`close_run`
    — the assistant's turn is written from a finaliser that may be running
    inside a cancelled request.
    """
    from workbench.db.models import Message, MessageCitation

    try:
        async with session_scope() as session:
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                run_id=run_id,
                model_used=model_used,
                latency_ms=latency_ms,
            )
            session.add(message)
            await session.flush()
            for c in citations or []:
                session.add(
                    MessageCitation(
                        message_id=message.id,
                        n=int(c.get("n", 0)),
                        chunk_id=str(c.get("chunk_id", "")),
                        doc_id=str(c.get("doc_id", "")),
                        doc_title=str(c.get("doc_title", "")),
                        page_no=int(c.get("page_no", 1)),
                        bbox=dict(c.get("bbox") or {}),
                        snippet=str(c.get("snippet", "")),
                        section_path=list(c.get("section_path") or []),
                        score=float(c.get("score", 0.0)),
                        retrieval_method=str(c.get("retrieval_method", "hybrid")),
                        confidence=float(c.get("confidence", 1.0)),
                    )
                )
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.updated_at = now()
    except Exception as exc:
        log.error(
            "message_persist_failed", conversation_id=conversation_id, role=role, error=str(exc)
        )


#: How much of a conversation a follow-up sees. Six turns, each cut short:
#: enough for "and the 2023 figure?" to mean something, small enough that the
#: evidence — the thing that should dominate the prompt — still does.
HISTORY_TURNS = 6
HISTORY_CHARS = 600


async def recent_history(session: Any, conversation_id: str) -> list[dict[str, str]]:
    """The last few turns, oldest first, for the model's context."""
    from sqlalchemy import select

    from workbench.db.models import Message

    rows = list(
        (
            await session.execute(
                select(Message)
                .where(col(Message.conversation_id) == conversation_id)
                .order_by(col(Message.created_at).desc())
                .limit(HISTORY_TURNS)
            )
        ).scalars()
    )
    rows.reverse()
    return [
        {"role": m.role, "content": (m.content or "")[:HISTORY_CHARS]} for m in rows if m.content
    ]
