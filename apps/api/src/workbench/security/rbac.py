"""Role-based access control and document classification filtering.

Two ideas carry the confidentiality guarantee:

* **The principal is the authority, never the prompt.** Permissions come from
  the authenticated user. No instruction inside a document, and no output from a
  model, can grant access to anything.
* **Classification filters run inside the query.** The clause that restricts
  which documents a user may see is injected into the vector-store filter and
  the SQL WHERE, not applied to results afterwards. Post-filtering leaks through
  result counts and relevance scores even when the text is withheld.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from workbench.core.classification import Classification
from workbench.core.errors import AuthorizationError, ConfigurationError


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity behind a request.

    Immutable and passed explicitly to every tool and retrieval call, so there
    is no ambient authority anywhere in the system.
    """

    user_id: str
    username: str
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    #: Defaults to the lowest level. A principal built without an explicit
    #: clearance must never inherit read access by omission.
    clearance: str = Classification.PUBLIC
    departments: frozenset[str] = frozenset()
    session_id: str | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def has_any(self, *permissions: str) -> bool:
        return any(p in self.permissions for p in permissions)

    def require(self, permission: str) -> None:
        """Raise unless the principal holds this permission."""
        if not self.has(permission):
            raise AuthorizationError(
                f"'{permission}' is required for this action",
                required_permission=permission,
            )

    @property
    def visible_classifications(self) -> list[str]:
        """Classifications this principal may retrieve, at or below clearance."""
        return Classification.at_or_below(self.clearance)

    def may_read(self, classification: str, departments: list[str] | None = None) -> bool:
        """Whether a document is visible to this principal."""
        if Classification.outranks(classification, self.clearance):
            return False
        # An empty department list means plant-wide; otherwise membership is
        # required. Admins are not special-cased here — an admin who should see
        # everything is given the departments to match.
        if departments and self.departments and not (set(departments) & self.departments):
            return False
        return True


#: A principal with no rights at all, used for unauthenticated contexts.
#: ``clearance="none"`` is not on the ladder, so it grants access to nothing.
ANONYMOUS = Principal(user_id="", username="anonymous", clearance="none")


@dataclass
class RbacConfig:
    """Roles and permissions as declared in config/rbac.yaml."""

    permissions: dict[str, str] = field(default_factory=dict)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    classifications: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> RbacConfig:
        if not path.is_file():
            raise ConfigurationError(f"RBAC config not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{path} is not valid YAML: {exc}") from exc

        permissions = {
            entry["code"]: entry.get("description", "")
            for entry in raw.get("permissions") or []
        }
        roles = dict(raw.get("roles") or {})

        # Catch a typo in the config at startup rather than as a silently
        # missing permission at request time.
        unknown: list[str] = []
        for role_name, role in roles.items():
            for code in role.get("permissions") or []:
                if code not in permissions:
                    unknown.append(f"role '{role_name}' grants undefined permission '{code}'")
        if unknown:
            raise ConfigurationError("invalid RBAC config:\n  - " + "\n  - ".join(unknown))

        return cls(
            permissions=permissions,
            roles=roles,
            classifications=list(raw.get("classifications") or Classification.ORDER),
        )

    def permissions_for(self, role_names: set[str]) -> frozenset[str]:
        codes: set[str] = set()
        for name in role_names:
            role = self.roles.get(name)
            if not role:
                continue
            granted = role.get("permissions") or []
            # A wildcard keeps the admin role from having to restate the whole
            # permission list every time a new one is introduced.
            if "admin:*" in granted:
                codes.update(self.permissions)
            else:
                codes.update(granted)
        return frozenset(codes)

    def clearance_for(self, role_names: set[str]) -> str:
        """The highest clearance among a user's roles."""
        best = Classification.PUBLIC
        for name in role_names:
            role = self.roles.get(name)
            if not role:
                continue
            candidate = str(role.get("clearance", Classification.PUBLIC))
            if Classification.clearance_rank(candidate) > Classification.clearance_rank(best):
                best = candidate
        return best


@dataclass(frozen=True, slots=True)
class AccessFilter:
    """The access-control clause, in a form each store can apply natively.

    Built once per retrieval and handed to both the vector store and the SQL
    query, so the two halves of hybrid search can never disagree about what a
    user is allowed to see.
    """

    classifications: list[str]
    departments: list[str]
    exclude_doc_ids: list[str] = field(default_factory=list)

    @classmethod
    def for_principal(
        cls, principal: Principal, *, exclude_doc_ids: list[str] | None = None
    ) -> AccessFilter:
        return cls(
            classifications=principal.visible_classifications,
            departments=sorted(principal.departments),
            exclude_doc_ids=exclude_doc_ids or [],
        )

    def to_qdrant(self) -> dict[str, Any]:
        """Render as a Qdrant filter, applied inside the ANN search itself."""
        must: list[dict[str, Any]] = [
            {"key": "classification", "match": {"any": self.classifications}}
        ]
        if self.departments:
            # An empty department list on a chunk means plant-wide access.
            must.append(
                {
                    "should": [
                        {"key": "departments", "match": {"any": self.departments}},
                        {"is_empty": {"key": "departments"}},
                    ]
                }
            )
        must_not: list[dict[str, Any]] = []
        if self.exclude_doc_ids:
            must_not.append({"key": "doc_id", "match": {"any": self.exclude_doc_ids}})

        query: dict[str, Any] = {"must": must}
        if must_not:
            query["must_not"] = must_not
        return query

    def describe(self) -> str:
        """Human-readable summary for the audit log."""
        parts = [f"classification<={self.classifications[-1] if self.classifications else 'none'}"]
        if self.departments:
            parts.append(f"departments={','.join(self.departments)}")
        if self.exclude_doc_ids:
            parts.append(f"excluding {len(self.exclude_doc_ids)} documents")
        return "; ".join(parts)
