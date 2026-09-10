"""SQLModel table definitions.

Every model must be imported here: Alembic's autogenerate walks
``SQLModel.metadata``, and a table that is never imported is silently omitted
from migrations.
"""

from workbench.db.models.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    Severity,
)
from workbench.db.models.conversation import (
    AgentRun,
    AgentStep,
    Conversation,
    Message,
    MessageCitation,
    RoutingDecision,
    RunStatus,
    ToolInvocation,
)
from workbench.db.models.document import (
    Chunk,
    Classification,
    Dataset,
    DocStatus,
    Document,
    DocumentBlock,
    DocumentPage,
    IngestionJob,
)
from workbench.db.models.governance import (
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactStatus,
    EvalResult,
    EvalRun,
    SandboxExecution,
    SandboxStatus,
    Setting,
)
from workbench.db.models.identity import (
    ApiKey,
    Permission,
    Role,
    RolePermissionLink,
    Session,
    User,
    UserRoleLink,
)

__all__ = [
    "AgentRun",
    "AgentStep",
    "ApiKey",
    "Approval",
    "ApprovalStatus",
    "Artifact",
    "ArtifactStatus",
    "AuditAction",
    "AuditDecision",
    "AuditEvent",
    "Chunk",
    "Classification",
    "Conversation",
    "Dataset",
    "DocStatus",
    "Document",
    "DocumentBlock",
    "DocumentPage",
    "EvalResult",
    "EvalRun",
    "IngestionJob",
    "Message",
    "MessageCitation",
    "Permission",
    "Role",
    "RolePermissionLink",
    "RoutingDecision",
    "RunStatus",
    "SandboxExecution",
    "SandboxStatus",
    "Session",
    "Setting",
    "Severity",
    "ToolInvocation",
    "User",
    "UserRoleLink",
]
