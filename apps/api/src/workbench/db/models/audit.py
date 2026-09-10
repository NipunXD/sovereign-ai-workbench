"""The audit log.

Append-only and hash-chained. Every entry commits to its predecessor, so
altering or deleting any historical row invalidates every hash after it and
``GET /api/v1/audit/verify`` reports the exact sequence number where the chain
breaks.

Two deliberate choices:

* **Payloads are stored as digests, not bodies.** An auditor needs to see that a
  restricted document was retrieved, by whom, and when — not its contents. This
  keeps the audit table small and means audit access is not a backdoor around
  document classification.
* **Ordering uses a database sequence, not a timestamp.** Two events in the same
  millisecond still have a definite order, which a hash chain requires.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, Index, Integer, String, text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlmodel import Field, SQLModel

from workbench.core.clock import now
from workbench.core.ids import prefixed_id
from workbench.db.base import UTCDateTime


class AuditAction:
    """Canonical action names. Strings, so new ones need no migration."""

    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    TOKEN_REFRESH = "auth.token_refresh"
    TOKEN_REUSE_DETECTED = "auth.token_reuse_detected"

    DOC_INGEST = "doc.ingest"
    DOC_READ = "doc.read"
    DOC_DOWNLOAD = "doc.download"
    DOC_DELETE = "doc.delete"
    DOC_RECLASSIFY = "doc.reclassify"

    RAG_SEARCH = "rag.search"
    MODEL_ROUTE = "model.route"
    TOOL_INVOKE = "tool.invoke"
    SANDBOX_EXEC = "sandbox.exec"
    ARTIFACT_GENERATE = "artifact.generate"
    APPROVAL_REQUEST = "approval.request"
    APPROVAL_DECIDE = "approval.decide"

    RUN_START = "run.start"
    RUN_FINISH = "run.finish"

    RBAC_DENY = "rbac.deny"
    CONFIG_CHANGE = "admin.config_change"
    USER_MANAGE = "admin.user_manage"


class AuditDecision:
    ALLOW = "allow"
    DENY = "deny"
    ERROR = "error"


class Severity:
    INFO = 0
    NOTICE = 1
    WARNING = 2
    CRITICAL = 3


class AuditEvent(SQLModel, table=True):
    """One immutable record of something that happened."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_ts", "ts"),
        Index("ix_audit_actor_ts", "actor_user_id", "ts"),
        Index("ix_audit_action_ts", "action", "ts"),
        Index("ix_audit_run", "run_id"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_decision", "decision"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("audit"), primary_key=True)

    #: Chain ordering. A gapless database sequence, independent of clock skew.
    seq: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, autoincrement=True, unique=True, nullable=False),
    )
    ts: datetime = Field(default_factory=now, sa_type=UTCDateTime)

    # --- who -----------------------------------------------------------------
    actor_user_id: str | None = Field(default=None, index=True)
    actor_username: str | None = None
    actor_roles: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(String()), nullable=False, server_default="{}"),
    )
    actor_ip: str | None = Field(default=None, sa_column=Column(INET, nullable=True))
    user_agent: str | None = None

    # --- correlation ---------------------------------------------------------
    session_id: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    request_id: str | None = None

    # --- what ----------------------------------------------------------------
    action: str = Field(index=True)
    resource_type: str | None = None
    resource_id: str | None = None
    decision: str = AuditDecision.ALLOW
    reason: str | None = None

    # --- context -------------------------------------------------------------
    model_used: str | None = None
    lane: str | None = None
    tool_name: str | None = None
    #: Digests, never bodies. See the module docstring.
    input_digest: str | None = Field(default=None, max_length=64)
    output_digest: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    severity: int = Field(default=Severity.INFO, sa_column=Column(Integer, nullable=False, server_default=sa_text("0")))
    audit_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("metadata", JSONB, nullable=False, server_default="{}")
    )

    # --- chain ---------------------------------------------------------------
    prev_hash: str = Field(default="0" * 64, max_length=64)
    hash: str = Field(default="", max_length=64)

    def chain_payload(self) -> dict[str, Any]:
        """The fields the hash commits to.

        Deliberately excludes ``hash`` itself and ``id``: the identifier is
        random and would prevent an independent verifier from recomputing the
        chain from an exported dump.
        """
        return {
            "seq": self.seq,
            "ts": self.ts,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor_username,
            "actor_roles": sorted(self.actor_roles),
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "decision": self.decision,
            "reason": self.reason,
            "model_used": self.model_used,
            "lane": self.lane,
            "tool_name": self.tool_name,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "severity": self.severity,
            "metadata": self.audit_metadata,
        }
