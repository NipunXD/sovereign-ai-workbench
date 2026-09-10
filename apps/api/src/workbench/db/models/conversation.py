"""Conversations, messages, agent runs and the trace they leave behind.

``agent_steps`` and ``routing_decisions`` are the durable record of *how* an
answer was produced. The live SSE stream is a convenience; these tables are what
lets a run be replayed weeks later, and what the router eval suite measures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Float, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from workbench.core.clock import now
from workbench.core.ids import prefixed_id
from workbench.db.base import UTCDateTime


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("conversation"), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    title: str = "New conversation"
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    archived_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("message"), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str = "user"
    content: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    content_format: str = "markdown"
    run_id: str | None = Field(default=None, index=True)
    model_used: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    parent_message_id: str | None = None
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class MessageCitation(SQLModel, table=True):
    """A resolved citation attached to an assistant message.

    Denormalised from the chunk on purpose: a citation must still render
    correctly after the source document is re-indexed or deleted, and an answer
    whose provenance silently disappears is worse than no provenance at all.
    """

    __tablename__ = "message_citations"
    __table_args__ = (Index("ix_citations_message", "message_id", "n"),)

    id: str = Field(default_factory=lambda: prefixed_id("cite"), primary_key=True)
    message_id: str = Field(foreign_key="messages.id", index=True)
    n: int = 1
    chunk_id: str = ""
    doc_id: str = ""
    doc_title: str = ""
    page_no: int = 1
    bbox: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    snippet: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    section_path: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    score: float = Field(default=0.0, sa_column=Column(Float, nullable=False, server_default=text("0")))
    retrieval_method: str = "hybrid"
    confidence: float = Field(default=1.0, sa_column=Column(Float, nullable=False, server_default=text("1")))


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_runs_status_started", "status", "started_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("run"), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: LangGraph checkpoint thread. Equal to the run id; named separately so the
    #: coupling is explicit rather than assumed.
    thread_id: str = ""
    status: str = Field(default=RunStatus.QUEUED, index=True)
    input: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    final_message_id: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    budget: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    validation: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    started_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    wall_ms: int | None = None


class AgentStep(SQLModel, table=True):
    """One node execution. Together these are the replayable trace."""

    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_steps_run_seq", "run_id", "seq"),)

    id: str = Field(default_factory=lambda: prefixed_id("step"), primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    seq: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    node: str = ""
    status: str = "running"
    input_digest: str | None = Field(default=None, max_length=64)
    output_digest: str | None = Field(default=None, max_length=64)
    model_used: str | None = None
    lane: str | None = None
    latency_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    started_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class RoutingDecision(SQLModel, table=True):
    """Why a particular model ran. Feeds the router eval and the admin UI."""

    __tablename__ = "routing_decisions"
    __table_args__ = (Index("ix_routing_lane_created", "lane", "created_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("route"), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    step_id: str | None = None
    lane: str = ""
    #: 0 deterministic, 1 lexical, 2 classifier. The distribution of this column
    #: is the headline number for whether the cascade is earning its keep.
    stage_decided: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    chosen_model: str = ""
    physical_model: str = ""
    provider: str = ""
    alternatives: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    confidence: float = Field(default=0.0, sa_column=Column(Float, nullable=False, server_default=text("0")))
    reason: str = ""
    features_digest: str | None = Field(default=None, max_length=64)
    decide_latency_ms: float = Field(default=0.0, sa_column=Column(Float, nullable=False, server_default=text("0")))
    swap_required: bool = False
    swap_latency_ms: float | None = None
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class ToolInvocation(SQLModel, table=True):
    __tablename__ = "tool_invocations"
    __table_args__ = (Index("ix_tools_run", "run_id"), Index("ix_tools_name_created", "tool_name", "created_at"))

    id: str = Field(default_factory=lambda: prefixed_id("tinv"), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    step_id: str | None = None
    tool_name: str = ""
    tool_version: str = "1.0.0"
    args_digest: str | None = Field(default=None, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    result_digest: str | None = Field(default=None, max_length=64)
    ok: bool = True
    error: str | None = None
    latency_ms: int | None = None
    approval_id: str | None = None
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
