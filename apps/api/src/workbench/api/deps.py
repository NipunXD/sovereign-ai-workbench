"""Request dependencies: authentication, authorisation and shared services.

``require_permission`` is the endpoint-level half of the access model. The other
two halves — the tool dispatcher and the retrieval filter — are enforced deeper,
because an endpoint check alone would not stop an agent from reaching data
through a tool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from workbench.core.errors import AuthenticationError, AuthorizationError
from workbench.core.logging import bind_request_context
from workbench.db.session import get_db
from workbench.security.audit import AuditLogger
from workbench.security.auth import TokenService
from workbench.security.rbac import Principal

#: auto_error=False so a missing header raises our own problem+json response
#: rather than FastAPI's default shape.
_bearer = HTTPBearer(auto_error=False)


async def get_session(session: Annotated[AsyncSession, Depends(get_db)]) -> AsyncSession:
    return session


def get_registry(request: Request) -> Any:
    return request.app.state.registry


def get_router(request: Request) -> Any:
    return request.app.state.router


def get_residency(request: Request) -> Any:
    return request.app.state.residency


def get_events(request: Request) -> Any:
    return request.app.state.events


def get_tokens(request: Request) -> TokenService:
    # Starlette's app.state is untyped by design; the lifespan sets this.
    return cast(TokenService, request.app.state.tokens)


async def get_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[AuditLogger]:
    yield AuditLogger(session)


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Resolve the caller from their access token.

    Permissions are carried in the token so an ordinary request needs no
    database round trip. The 15-minute lifetime bounds how long a revoked
    permission stays effective.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("An access token is required.")

    claims = request.app.state.tokens.decode(credentials.credentials)
    principal = Principal(
        user_id=claims.subject,
        username=claims.username,
        roles=frozenset(claims.roles),
        permissions=frozenset(claims.permissions),
        clearance=claims.clearance,
        departments=frozenset(claims.departments),
        session_id=claims.session_id,
    )
    # Every log line for this request now carries who made it.
    bind_request_context(actor=principal.username, user_id=principal.user_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
Audit = Annotated[AuditLogger, Depends(get_audit)]


def require_permission(*permissions: str) -> Callable[..., Any]:
    """Dependency requiring every named permission.

    Denials are audited: an attempt to reach something out of scope is exactly
    what a reviewer wants to see, and silence would hide it.
    """

    async def dependency(
        principal: CurrentPrincipal,
        audit: Audit,
        request: Request,
    ) -> Principal:
        missing = {p for p in permissions if not principal.has(p)}
        if missing:
            await audit.deny(
                "rbac.deny",
                reason=f"missing {', '.join(sorted(missing))}",
                actor_user_id=principal.user_id,
                actor_username=principal.username,
                actor_roles=sorted(principal.roles),
                resource_type="endpoint",
                resource_id=str(request.url.path),
            )
            raise AuthorizationError(
                f"This action requires {', '.join(sorted(missing))}.",
                required_permission=sorted(missing)[0],
            )
        return principal

    return dependency
