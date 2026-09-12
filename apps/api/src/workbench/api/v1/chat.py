"""Streaming chat — the primary endpoint.

Returns ``text/event-stream``. The browser sees the agent think, retrieve, call
tools and answer as it happens, rather than waiting on a spinner for a minute
while a local model works.

The answer is streamed twice, deliberately: raw ``token`` events as the model
produces them, then a final ``answer`` event carrying the citation-resolved
text. The model emits ``[[cite:chunk_id]]`` markers which cannot be rewritten
until the whole sentence exists, so the UI renders tokens live for
responsiveness and swaps in the resolved version at the end.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlmodel import col

from workbench.agent.runner import AgentRunner
from workbench.agent.state import Budget
from workbench.api.deps import CurrentPrincipal, DbSession, require_permission
from workbench.api.sse import SSE_HEADERS, EventName, format_sse, with_heartbeat
from workbench.api.v1.runs import close_run, open_run, persist_message, recent_history
from workbench.artifacts.records import persist_artifact
from workbench.core.errors import NotFoundError
from workbench.core.ids import prefixed_id
from workbench.core.logging import get_logger
from workbench.db.models import AuditAction, AuditDecision, Conversation, RunStatus
from workbench.db.session import session_scope
from workbench.security import request_policy
from workbench.security.audit import AuditLogger
from workbench.security.rbac import Principal

log = get_logger(__name__)
router = APIRouter(tags=["chat"])


class ChatOptions(BaseModel):
    profile: str | None = None
    lane_hint: str | None = None
    max_steps: int = Field(default=6, ge=1, le=12)
    max_tool_calls: int = Field(default=12, ge=1, le=30)
    max_wall_s: float = Field(default=300.0, ge=10.0, le=900.0)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    options: ChatOptions = Field(default_factory=ChatOptions)


def build_runner(request: Request) -> AgentRunner:
    """Assemble the runner from the services built at startup."""
    state = request.app.state
    return AgentRunner(
        router=state.router,
        registry=state.registry,
        tools=state.tools,
        retriever=getattr(state, "retriever", None),
        residency=state.residency,
        approval_gate=getattr(state, "approval_gate", None),
    )


#: Strong references to in-flight finalisation tasks. asyncio holds only weak
#: ones, so without this a cleanup task can be collected before it runs.
_FINALISERS: set[asyncio.Task[None]] = set()


def _finalise_in_background(
    *,
    run_id: str,
    conversation_id: str,
    status: str,
    plan: dict[str, Any],
    budget: dict[str, Any],
    validation: dict[str, Any],
    failure: dict[str, Any] | None,
    citations: int,
    principal: Principal,
    trace: list[dict[str, Any]] | None = None,
    answer: str = "",
    answer_citations: list[dict[str, Any]] | None = None,
    model_used: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Close out a run on a task of its own.

    Called from the stream's `finally`, which may be executing inside a
    cancelled task. Everything here therefore runs detached and on its own
    database session: the record of how a run ended must not depend on the
    request that ended.
    """

    async def finalise() -> None:
        await close_run(
            run_id=run_id,
            status=status,
            plan=plan,
            budget=budget,
            validation=validation,
            error=failure,
            trace=trace,
        )
        # The assistant's turn, with its citations, so the conversation can be
        # reopened. Written even for a cancelled or failed run: whatever
        # arrived before the cut is what the person saw, and a saved chat that
        # silently drops a turn misrepresents what happened.
        if answer or failure:
            await persist_message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                run_id=run_id,
                model_used=model_used,
                latency_ms=latency_ms,
                citations=answer_citations or [],
            )
        try:
            async with session_scope() as session:
                await AuditLogger(session).log(
                    AuditAction.RUN_FINISH,
                    actor_user_id=principal.user_id,
                    actor_username=principal.username,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    resource_type="run",
                    resource_id=run_id,
                    output_digest=None,
                    metadata={"status": status, "citations": citations},
                )
        except Exception as exc:
            # Failing to write the closing audit record must not take anything
            # else down; the run row above already records the outcome.
            log.error("run_finish_audit_failed", run_id=run_id, error=str(exc))

    task = asyncio.ensure_future(finalise())
    _FINALISERS.add(task)
    task.add_done_callback(_FINALISERS.discard)


async def _refused(
    *,
    run_id: str,
    conversation_id: str,
    principal: Principal,
    message: str,
    verdict: request_policy.PolicyVerdict,
) -> AsyncIterator[str]:
    """A refused request, recorded and replayable like any other turn.

    It still opens and closes a run and still persists both messages, so the
    conversation reads back correctly and the refusal is part of its history
    rather than something that happened invisibly.
    """
    await open_run(
        run_id=run_id, conversation_id=conversation_id, principal=principal, message=message
    )
    await persist_message(
        conversation_id=conversation_id, role="user", content=message, run_id=run_id
    )

    stored: list[dict[str, Any]] = [
        {
            "name": EventName.RUN_STARTED,
            "at_ms": 0,
            "data": {"run_id": run_id, "conversation_id": conversation_id},
        },
        {
            "name": EventName.POLICY_REFUSED,
            "at_ms": 0,
            "data": {"category": verdict.category, "message": verdict.message},
        },
        {
            "name": EventName.ANSWER,
            "at_ms": 0,
            "data": {"text": verdict.message, "citations": []},
        },
        {
            "name": EventName.RUN_FINISHED,
            "at_ms": 0,
            # Shaped like any other run_finished. A partial payload here
            # crashed the footer, which reads budget.tool_calls_used without
            # asking whether the run got far enough to have a budget.
            "data": {
                "status": "refused",
                "wall_ms": 0,
                "evidence_used": 0,
                "budget": {"tool_calls_used": 0, "retrieve_loops": 0, "validate_loops": 0},
            },
        },
    ]
    for index, event in enumerate(stored):
        yield format_sse(str(event["name"]), event["data"], seq=index)

    await persist_message(
        conversation_id=conversation_id,
        role="assistant",
        content=verdict.message,
        run_id=run_id,
        latency_ms=0,
    )
    await close_run(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        trace=stored,
        validation={"policy_refused": verdict.category},
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("chat:use"))],
) -> StreamingResponse:
    """Run one request and stream its trace."""
    runner = build_runner(request)
    run_id = prefixed_id("run")
    audit = AuditLogger(session)

    # Continuing a conversation means it must be *this person's*. Without the
    # check, a run could be attached to anybody's conversation by id, and its
    # answer would then appear in their saved history.
    if payload.conversation_id:
        owned = (
            await session.execute(
                select(Conversation).where(
                    col(Conversation.id) == payload.conversation_id,
                    col(Conversation.user_id) == principal.user_id,
                    col(Conversation.archived_at).is_(None),
                )
            )
        ).scalar_one_or_none()
        if owned is None:
            raise NotFoundError("No such conversation.")
        conversation_id = owned.id
    else:
        conversation_id = prefixed_id("conversation")

    # What was said before, so a follow-up ("and for CML-06?") has something
    # to follow. Read before this turn is written.
    history = await recent_history(session, conversation_id) if payload.conversation_id else []

    # The policy gate runs before anything is opened, routed or retrieved: a
    # request to defeat a safety interlock should cost nothing and leave a
    # record, not two minutes of GPU and a polite answer about the corpus.
    verdict = request_policy.evaluate(payload.message)
    if not verdict.allowed:
        await audit.log(
            AuditAction.POLICY_REFUSE,
            decision=AuditDecision.DENY,
            actor_user_id=principal.user_id,
            actor_username=principal.username,
            actor_roles=sorted(principal.roles),
            run_id=run_id,
            conversation_id=conversation_id,
            resource_type="conversation",
            resource_id=conversation_id,
            reason=verdict.category,
            metadata={"matched": verdict.matched, "message_preview": payload.message[:200]},
        )
        await session.commit()
        return StreamingResponse(
            _refused(
                run_id=run_id,
                conversation_id=conversation_id,
                principal=principal,
                message=payload.message,
                verdict=verdict,
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    await audit.log(
        AuditAction.RUN_START,
        actor_user_id=principal.user_id,
        actor_username=principal.username,
        actor_roles=sorted(principal.roles),
        run_id=run_id,
        conversation_id=conversation_id,
        resource_type="conversation",
        resource_id=conversation_id,
        metadata={"message_preview": payload.message[:200]},
    )
    await session.commit()

    await open_run(
        run_id=run_id,
        conversation_id=conversation_id,
        principal=principal,
        message=payload.message,
    )
    await persist_message(
        conversation_id=conversation_id, role="user", content=payload.message, run_id=run_id
    )

    async def stream() -> AsyncIterator[str]:
        seq = 0
        citations: list[dict[str, Any]] = []
        status = RunStatus.SUCCEEDED
        plan: dict[str, Any] = {}
        budget_used: dict[str, Any] = {}
        validation: dict[str, Any] = {}
        failure: dict[str, Any] | None = None
        # The replayable record. Tokens are folded into the final answer and
        # reasoning into one event at the end, so what is stored is what the
        # browser needs to rebuild the view, not the firehose that built it.
        stored: list[dict[str, Any]] = []
        reasoning_parts: list[str] = []
        answer_text = ""
        answer_citations: list[dict[str, Any]] = []
        model_used: str | None = None
        started = time.monotonic()
        # The gate decides before the tool runs, so an artifact produced after
        # an approval in this run is already approved — it should not sit in
        # the queue a second time asking about the same decision.
        approved_in_run = False

        try:
            async for event in runner.run(
                user_input=payload.message,
                principal=principal,
                run_id=run_id,
                conversation_id=conversation_id,
                history=history,
                budget=Budget(
                    max_tool_calls=payload.options.max_tool_calls,
                    max_wall_s=payload.options.max_wall_s,
                ),
            ):
                seq += 1
                if event.name == EventName.TOKEN:
                    pass
                elif event.name == EventName.REASONING:
                    reasoning_parts.append(str(event.data.get("text", "")))
                else:
                    data = dict(event.data)
                    if event.name == EventName.RUN_STARTED:
                        data["conversation_id"] = conversation_id
                    stored.append(
                        {
                            "name": event.name,
                            "data": data,
                            "at_ms": int((time.monotonic() - started) * 1000),
                        }
                    )
                if event.name == EventName.ROUTE_DECISION and model_used is None:
                    model_used = str(
                        event.data.get("physical_model") or event.data.get("model") or ""
                    )
                if event.name == EventName.ANSWER:
                    answer_text = str(event.data.get("text", ""))
                    answer_citations = list(event.data.get("citations") or [])
                if event.name == EventName.CITATION:
                    citations.append(event.data)
                elif event.name == EventName.APPROVAL_REQUIRED:
                    if event.data.get("approved") is True:
                        approved_in_run = True
                elif event.name == EventName.ARTIFACT_CREATED:
                    await _persist_artifact(
                        event.data,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        principal=principal,
                        approved=approved_in_run,
                    )
                elif event.name == EventName.PLAN_CREATED:
                    plan = event.data
                elif event.name == EventName.VALIDATION:
                    validation = event.data
                elif event.name == EventName.RUN_FINISHED:
                    status = event.data.get("status", RunStatus.SUCCEEDED)
                    budget_used = event.data.get("budget") or {}
                if event.name == EventName.RUN_STARTED:
                    yield format_sse(
                        event.name, {**event.data, "conversation_id": conversation_id}, seq=seq
                    )
                else:
                    yield format_sse(event.name, event.data, seq=seq)
        except asyncio.CancelledError:
            # The client went away — a closed tab, a navigation, a reload. This
            # is not caught by `except Exception`: CancelledError is a
            # BaseException, so it used to pass straight through to `finally`
            # while the request's own session was already being torn down, and
            # the closing record was lost with it. That is how a run ended up
            # with a start and no end, and a browser left spinning on a run
            # that had stopped.
            log.info("chat_stream_cancelled", run_id=run_id)
            status = RunStatus.CANCELLED
            failure = {"reason": "the client disconnected before the run finished"}
            raise
        except Exception as exc:
            log.exception("chat_stream_failed", run_id=run_id, error=str(exc))
            status = RunStatus.FAILED
            failure = {"code": type(exc).__name__, "message": str(exc)[:500]}
            seq += 1
            yield format_sse(
                EventName.ERROR,
                {"code": type(exc).__name__, "message": "The request could not be completed."},
                seq=seq,
            )
        finally:
            # Detached on purpose. When the client disconnects this generator is
            # cancelled, and in a cancelled task *every* await raises
            # CancelledError at its first suspension point — so awaiting the
            # closing writes here runs none of them. The symptom is precise and
            # confusing: the cancellation is logged, and the run stays marked
            # `running` forever with nothing to say why.
            #
            # Handing the work to an independent task detaches it from this
            # one's cancellation. The reference is held until it finishes
            # because the loop only keeps weak references to tasks, and a
            # garbage-collected cleanup task is the same bug with extra steps.
            reasoning = "".join(reasoning_parts)
            if reasoning:
                stored.append(
                    {
                        "name": EventName.REASONING,
                        "data": {"text": reasoning[:20000]},
                        "at_ms": int((time.monotonic() - started) * 1000),
                    }
                )
            _finalise_in_background(
                run_id=run_id,
                conversation_id=conversation_id,
                status=status,
                plan=plan,
                budget=budget_used,
                validation=validation,
                failure=failure,
                citations=len(citations),
                principal=principal,
                trace=stored,
                answer=answer_text,
                answer_citations=answer_citations,
                model_used=model_used,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    # Wrapped so the connection carries traffic during the run's silences. The
    # longest is the approval wait — three minutes of nothing by design, which
    # without this reads as a dead stream to both proxies and the browser.
    return StreamingResponse(
        with_heartbeat(stream()), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.get("/runs/{run_id}/events")
async def replay_events(
    run_id: str,
    request: Request,
    principal: CurrentPrincipal,
    last_event_id: int = 0,
) -> StreamingResponse:
    """Replay a run's trace, for a client that dropped mid-stream."""
    from workbench.api.sse import event_stream

    bus = request.app.state.events
    return StreamingResponse(
        event_stream(bus, run_id, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _persist_artifact(
    payload: dict[str, Any],
    *,
    run_id: str,
    conversation_id: str,
    principal: Principal,
    approved: bool = False,
) -> None:
    """Record a generated artifact. See workbench.artifacts.records."""
    await persist_artifact(
        payload,
        run_id=run_id,
        conversation_id=conversation_id,
        created_by=principal.user_id,
        approved=approved,
    )
