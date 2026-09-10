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

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from workbench.agent.runner import AgentRunner
from workbench.agent.state import Budget
from workbench.api.deps import CurrentPrincipal, DbSession, require_permission
from workbench.api.sse import SSE_HEADERS, EventName, format_sse
from workbench.core.ids import prefixed_id
from workbench.core.logging import get_logger
from workbench.db.models import AuditAction
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
    conversation_id = payload.conversation_id or prefixed_id("conversation")
    audit = AuditLogger(session)

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

    async def stream() -> AsyncIterator[str]:
        seq = 0
        citations: list[dict[str, Any]] = []
        status = "succeeded"
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
                budget=Budget(
                    max_tool_calls=payload.options.max_tool_calls,
                    max_wall_s=payload.options.max_wall_s,
                ),
            ):
                seq += 1
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
                elif event.name == EventName.RUN_FINISHED:
                    status = event.data.get("status", "succeeded")
                yield format_sse(event.name, event.data, seq=seq)
        except Exception as exc:
            log.exception("chat_stream_failed", run_id=run_id, error=str(exc))
            status = "failed"
            seq += 1
            yield format_sse(
                EventName.ERROR,
                {"code": type(exc).__name__, "message": "The request could not be completed."},
                seq=seq,
            )
        finally:
            try:
                await audit.log(
                    AuditAction.RUN_FINISH,
                    actor_user_id=principal.user_id,
                    actor_username=principal.username,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    resource_type="run",
                    resource_id=run_id,
                    output_digest=None,
                    metadata={"status": status, "citations": len(citations)},
                )
                await session.commit()
            except Exception as exc:
                # Failing to write the closing audit record must not corrupt the
                # response the user already received.
                log.error("run_finish_audit_failed", run_id=run_id, error=str(exc))

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


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
    """Record a generated artifact so it outlives the stream.

    Written on its own session: the request transaction may still be streaming,
    and an artifact that exists on disk but has no row is invisible to the
    approval queue and undownloadable.
    """
    from sqlalchemy import select

    from workbench.db.models import Artifact, ArtifactStatus
    from workbench.db.session import session_scope

    sha256 = str(payload.get("sha256") or "")
    if not sha256:
        return

    try:
        async with session_scope() as session:
            existing = (
                await session.execute(select(Artifact).where(Artifact.sha256 == sha256))
            ).scalar_one_or_none()
            if existing is not None:
                return
            session.add(
                Artifact(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    created_by=principal.user_id,
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
