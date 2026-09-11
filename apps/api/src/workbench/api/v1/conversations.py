"""Saved conversations.

A conversation belongs to the person who had it, and to nobody else — not to
an admin, not to an auditor. The audit log already records that a run
happened and what it touched; the *content* of what someone asked is theirs.
Every query here filters on the caller's user id, and a conversation that is
not theirs is reported as not found rather than forbidden, so the endpoint
does not confirm that someone else's conversation exists.

What comes back for a single conversation is enough to rebuild the live view
exactly: the messages with their resolved citations, and for each run its
stored event stream, which the browser replays through the same reducer it
used the first time. A saved chat that keeps the answer and loses its
evidence map would be a transcript, not a record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlmodel import col

from workbench.api.deps import DbSession, require_permission
from workbench.core.clock import now
from workbench.core.errors import NotFoundError
from workbench.db.models import AgentRun, Artifact, Conversation, Message, MessageCitation
from workbench.security.rbac import Principal

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    artifact_count: int
    preview: str


class StoredCitation(BaseModel):
    n: int
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: str = ""
    page_no: int
    bbox: dict[str, Any]
    snippet: str
    section_path: list[str]
    score: float
    retrieval_method: str
    confidence: float


class StoredMessage(BaseModel):
    id: str
    role: str
    content: str
    run_id: str | None
    model_used: str | None
    latency_ms: int | None
    created_at: datetime
    citations: list[StoredCitation] = Field(default_factory=list)


class StoredRun(BaseModel):
    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    wall_ms: int | None
    plan: dict[str, Any]
    validation: dict[str, Any]
    #: The replayable event stream: [{name, data, at_ms}], tokens excluded.
    trace: list[dict[str, Any]]


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[StoredMessage]
    runs: list[StoredRun]
    artifacts: list[dict[str, Any]]


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


async def _owned(session: DbSession, conversation_id: str, principal: Principal) -> Conversation:
    """The conversation, if it is the caller's. Not found otherwise — deliberately."""
    conversation = (
        await session.execute(
            select(Conversation).where(
                col(Conversation.id) == conversation_id,
                col(Conversation.user_id) == principal.user_id,
                col(Conversation.archived_at).is_(None),
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("No such conversation.")
    return conversation


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("chat:use"))],
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConversationSummary]:
    query = (
        select(Conversation)
        .where(
            col(Conversation.user_id) == principal.user_id,
            col(Conversation.archived_at).is_(None),
            # A session with no stored turn has nothing to reopen. Rows like
            # that exist — a run cut off before its first message was written,
            # or one from before turns were kept — and each is a dead link.
            exists().where(col(Message.conversation_id) == col(Conversation.id)),
        )
        .order_by(col(Conversation.updated_at).desc())
        .limit(limit)
    )
    if search:
        query = query.where(col(Conversation.title).ilike(f"%{search}%"))
    conversations = list((await session.execute(query)).scalars())
    if not conversations:
        return []

    ids = [c.id for c in conversations]
    counts = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(col(Message.conversation_id), func.count(col(Message.id)))
                .where(col(Message.conversation_id).in_(ids))
                .group_by(col(Message.conversation_id))
            )
        ).all()
    }
    artifact_counts = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(col(Artifact.conversation_id), func.count(col(Artifact.id)))
                .where(col(Artifact.conversation_id).in_(ids))
                .group_by(col(Artifact.conversation_id))
            )
        ).all()
    }
    # The last assistant line, as a preview.
    previews: dict[str, str] = {}
    for message in (
        await session.execute(
            select(Message)
            .where(col(Message.conversation_id).in_(ids), col(Message.role) == "assistant")
            .order_by(col(Message.created_at).desc())
        )
    ).scalars():
        previews.setdefault(message.conversation_id, message.content[:140])

    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=counts.get(c.id, 0),
            artifact_count=artifact_counts.get(c.id, 0),
            preview=previews.get(c.id, ""),
        )
        for c in conversations
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("chat:use"))],
) -> ConversationDetail:
    conversation = await _owned(session, conversation_id, principal)

    messages = list(
        (
            await session.execute(
                select(Message)
                .where(col(Message.conversation_id) == conversation.id)
                .order_by(col(Message.created_at))
            )
        ).scalars()
    )
    citations: dict[str, list[StoredCitation]] = {}
    if messages:
        for citation in (
            await session.execute(
                select(MessageCitation)
                .where(col(MessageCitation.message_id).in_([m.id for m in messages]))
                .order_by(col(MessageCitation.n))
            )
        ).scalars():
            citations.setdefault(citation.message_id, []).append(
                StoredCitation(
                    n=citation.n,
                    chunk_id=citation.chunk_id,
                    doc_id=citation.doc_id,
                    doc_title=citation.doc_title,
                    page_no=citation.page_no,
                    bbox=citation.bbox,
                    snippet=citation.snippet,
                    section_path=citation.section_path,
                    score=citation.score,
                    retrieval_method=citation.retrieval_method,
                    confidence=citation.confidence,
                )
            )

    runs = list(
        (
            await session.execute(
                select(AgentRun)
                .where(col(AgentRun.conversation_id) == conversation.id)
                .order_by(col(AgentRun.started_at))
            )
        ).scalars()
    )
    artifacts = list(
        (
            await session.execute(
                select(Artifact)
                .where(col(Artifact.conversation_id) == conversation.id)
                .order_by(col(Artifact.created_at))
            )
        ).scalars()
    )

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            StoredMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                run_id=m.run_id,
                model_used=m.model_used,
                latency_ms=m.latency_ms,
                created_at=m.created_at,
                citations=citations.get(m.id, []),
            )
            for m in messages
        ],
        runs=[
            StoredRun(
                id=r.id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                wall_ms=r.wall_ms,
                plan=r.plan or {},
                validation=r.validation or {},
                trace=r.trace or [],
            )
            for r in runs
        ],
        artifacts=[
            {
                "artifact_id": a.sha256,
                "run_id": a.run_id,
                "filename": a.filename,
                "kind": a.kind,
                "mime": a.mime,
                "sha256": a.sha256,
                "size_bytes": a.size_bytes,
                "status": a.status,
                "download_url": f"/api/v1/artifacts/{a.sha256}/download",
                "provenance": a.provenance or {},
            }
            for a in artifacts
        ],
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: str,
    payload: RenameRequest,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("chat:use"))],
) -> ConversationSummary:
    conversation = await _owned(session, conversation_id, principal)
    conversation.title = payload.title.strip()
    conversation.updated_at = now()
    await session.flush()
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=0,
        artifact_count=0,
        preview="",
    )


@router.delete("/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: str,
    session: DbSession,
    principal: Annotated[Principal, Depends(require_permission("chat:use"))],
) -> None:
    """Archive rather than delete: the audit log references its runs."""
    conversation = await _owned(session, conversation_id, principal)
    conversation.archived_at = now()
    await session.flush()
