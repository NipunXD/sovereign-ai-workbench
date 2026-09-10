"""Users, roles, permissions and sessions.

Authentication is local by design: an air-gapped deployment has no external
identity provider to federate with. The role/permission tables are seeded from
``config/rbac.yaml`` and can then be edited through the admin UI, with every
change written to the audit log.
"""

# NOTE: no `from __future__ import annotations` here. PEP 563 turns
# `list["Role"]` into a nested-quoted string that SQLAlchemy cannot resolve when
# it configures relationship mappers, so this module keeps runtime annotations.

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, Index, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, Relationship, SQLModel

from workbench.core.clock import now
from workbench.core.ids import prefixed_id
from workbench.db.base import UTCDateTime

if TYPE_CHECKING:
    pass


class UserRoleLink(SQLModel, table=True):
    """Grant of a role to a user, recorded with who granted it and when."""

    __tablename__ = "user_roles"

    user_id: str = Field(foreign_key="users.id", primary_key=True)
    role_id: str = Field(foreign_key="roles.id", primary_key=True)
    granted_by: str | None = Field(default=None, foreign_key="users.id")
    granted_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class RolePermissionLink(SQLModel, table=True):
    __tablename__ = "role_permissions"

    role_id: str = Field(foreign_key="roles.id", primary_key=True)
    permission_id: str = Field(foreign_key="permissions.id", primary_key=True)


class Permission(SQLModel, table=True):
    __tablename__ = "permissions"

    id: str = Field(default_factory=lambda: prefixed_id("perm"), primary_key=True)
    code: str = Field(unique=True, index=True)
    description: str = ""

    roles: list["Role"] = Relationship(
        back_populates="permissions",
        link_model=RolePermissionLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: str = Field(default_factory=lambda: prefixed_id("role"), primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str = ""
    #: Highest document classification this role may retrieve.
    clearance: str = "internal"
    #: System roles come from rbac.yaml and cannot be deleted in the UI.
    is_system: bool = True
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)

    # Eager by default: under asyncio a lazy load raises MissingGreenlet the
    # moment anything touches the collection outside an await, and permissions
    # are read on essentially every authenticated request anyway.
    permissions: list[Permission] = Relationship(
        back_populates="roles",
        link_model=RolePermissionLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    # user_roles carries two foreign keys to users — the grantee and whoever
    # granted the role — so the join has to be stated explicitly.
    users: list["User"] = Relationship(
        back_populates="roles",
        link_model=UserRoleLink,
        sa_relationship_kwargs={
            "primaryjoin": "Role.id == UserRoleLink.role_id",
            "secondaryjoin": "User.id == UserRoleLink.user_id",
        },
    )


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_active", "is_active"),)

    id: str = Field(default_factory=lambda: prefixed_id("user"), primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str | None = Field(default=None, unique=True, index=True)
    full_name: str = ""
    #: argon2id. Never logged, never returned by any endpoint.
    password_hash: str = ""
    is_active: bool = True

    #: Effective clearance. Denormalised from roles at write time so retrieval
    #: filters do not need a join on the hot path.
    clearance: str = "internal"
    departments: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )

    must_change_password: bool = False
    failed_attempts: int = 0
    locked_until: datetime | None = Field(default=None, sa_type=UTCDateTime)
    last_login_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)

    roles: list[Role] = Relationship(
        back_populates="users",
        link_model=UserRoleLink,
        sa_relationship_kwargs={
            "primaryjoin": "User.id == UserRoleLink.user_id",
            "secondaryjoin": "Role.id == UserRoleLink.role_id",
            # Roles are needed on nearly every authenticated request; a lazy
            # load here would mean an extra round trip per request.
            "lazy": "selectin",
        },
    )

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > now()


class Session(SQLModel, table=True):
    """A refresh-token session.

    Only the hash of the refresh token is stored, and ``rotated_from`` records
    the chain: presenting an already-rotated token indicates theft, and the
    whole chain gets revoked.
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)

    id: str = Field(default_factory=lambda: prefixed_id("session"), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    refresh_token_hash: str = Field(index=True)
    issued_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    expires_at: datetime = Field(sa_type=UTCDateTime)
    revoked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    rotated_from: str | None = Field(default=None)
    ip: str | None = None
    user_agent: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > now()


class ApiKey(SQLModel, table=True):
    """Long-lived credential for service integrations inside the plant network."""

    __tablename__ = "api_keys"

    id: str = Field(default_factory=lambda: prefixed_id("apik"), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str = ""
    key_hash: str = Field(index=True)
    scopes: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(String()), nullable=False, server_default="{}")
    )
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    last_used_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    revoked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
