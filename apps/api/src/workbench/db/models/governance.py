"""Artifacts, approvals, sandbox executions and eval records.

Everything that produces a durable output or runs code lands here, because
these are the actions an MRPL reviewer will want to see evidence for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Float, Index, Integer, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from workbench.core.clock import now
from workbench.core.ids import prefixed_id
from workbench.db.base import UTCDateTime


class ArtifactStatus:
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SandboxStatus:
    OK = "ok"
    TIMEOUT = "timeout"
    MEMORY = "memory"
    ERROR = "error"
    POLICY = "policy"


class Artifact(SQLModel, table=True):
    """A generated Word/Excel/PowerPoint/PDF/chart output."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_status_created", "status", "created_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("artifact"), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    conversation_id: str | None = Field(default=None, index=True)
    created_by: str | None = Field(default=None, foreign_key="users.id")

    kind: str = "docx"
    filename: str = ""
    mime: str = ""
    sha256: str = Field(default="", max_length=64, index=True)
    size_bytes: int = 0
    storage_path: str = ""

    spec: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    #: Models used, source documents with page numbers, retrieval settings,
    #: approver and timestamps. Rendered into the document itself as well, so
    #: the provenance travels with the file once it leaves the system.
    provenance: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )

    status: str = Field(default=ArtifactStatus.DRAFT, index=True)
    version: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default=sa_text("1"))
    )
    supersedes_id: str | None = None
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class Approval(SQLModel, table=True):
    """A human decision gate.

    ``expires_at`` exists so a run can never wait forever: a periodic task
    expires stale approvals and resumes the graph with a rejection, which means
    every run terminates one way or another.
    """

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_requested", "status", "requested_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("approval"), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    step_id: str | None = None
    kind: str = "artifact"  # tool | artifact | final
    subject_type: str = ""
    subject_id: str = ""

    requested_by: str | None = Field(default=None, foreign_key="users.id")
    requested_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    payload_summary: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    payload_digest: str | None = Field(default=None, max_length=64)

    #: Everything needed to carry out the approved action later, without the
    #: run that requested it. Held because an approver is a person with a day
    #: of their own: the requester's connection lasts minutes, and a decision
    #: that only counts while they wait is a decision made under the wrong
    #: pressure. The arguments are bound before approval is asked for, so this
    #: is also what the approver is agreeing to — a summary of an action whose
    #: details are still undecided is not something anyone can sign off.
    deferred: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )

    status: str = Field(default=ApprovalStatus.PENDING, index=True)
    decided_by: str | None = Field(default=None, foreign_key="users.id")
    decided_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    comment: str | None = None
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)

    #: Set once the approved action has actually been carried out, so it
    #: happens exactly once whoever gets there first — the waiting run or the
    #: approval itself.
    executed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    execution_result: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )


class SandboxExecution(SQLModel, table=True):
    """Evidence that generated code ran under known constraints.

    ``limits`` records the exact container configuration applied, so a reviewer
    can confirm after the fact that the code had no network access.
    """

    __tablename__ = "sandbox_executions"

    id: str = Field(default_factory=lambda: prefixed_id("execution"), primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    step_id: str | None = None
    code_digest: str = Field(default="", max_length=64)
    image_digest: str = ""
    limits: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    exit_code: int | None = None
    status: str = SandboxStatus.OK
    stdout_truncated: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    stderr_truncated: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    output_files: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]")
    )
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: str = Field(default_factory=lambda: prefixed_id("eval"), primary_key=True)
    suite: str = Field(index=True)
    git_sha: str | None = None
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    summary: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    started_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"
    __table_args__ = (Index("ix_eval_results_run_metric", "eval_run_id", "metric"),)

    id: str = Field(default_factory=lambda: prefixed_id("evres"), primary_key=True)
    eval_run_id: str = Field(foreign_key="eval_runs.id", index=True)
    case_id: str = ""
    metric: str = ""
    value: float = Field(
        default=0.0, sa_column=Column(Float, nullable=False, server_default=sa_text("0"))
    )
    passed: bool = True
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )


class Setting(SQLModel, table=True):
    """Runtime settings editable through the admin UI."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
