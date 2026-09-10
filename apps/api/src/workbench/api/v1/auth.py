"""Authentication endpoints.

Refresh tokens live in an httpOnly, SameSite=Strict cookie and are rotated on
every use. Presenting a token that has already been rotated means it was
captured, so the whole session chain is revoked rather than just that token.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlmodel import col

from workbench.api.deps import Audit, CurrentPrincipal, DbSession
from workbench.core.clock import now
from workbench.core.errors import AccountLockedError, AuthenticationError
from workbench.core.logging import get_logger
from workbench.db.models import AuditAction, Role, Session, User
from workbench.db.models.audit import Severity
from workbench.security.auth import (
    generate_refresh_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "workbench_refresh"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — the RFC 6750 scheme name, not a secret
    expires_in: int
    username: str
    roles: list[str]
    permissions: list[str]
    clearance: str


class MeResponse(BaseModel):
    user_id: str
    username: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    clearance: str
    departments: list[str]


async def _record_failed_attempt(
    user_id: str, max_attempts: int, lockout_minutes: int
) -> tuple[int, bool]:
    """Increment the failure counter durably, locking the account at the limit.

    Uses its own connection because the caller raises immediately afterwards.
    The increment is done in SQL rather than read-modify-write so concurrent
    attempts cannot both read the same value and lose one.
    """
    from sqlalchemy import update

    from workbench.db.session import get_session_factory

    try:
        factory = get_session_factory()
    except RuntimeError:
        return 1, False

    async with factory() as session:
        attempts = (
            await session.execute(
                update(User)
                .where(col(User.id) == user_id)
                .values(failed_attempts=User.failed_attempts + 1)
                .returning(col(User.failed_attempts))
            )
        ).scalar_one()

        locked = attempts >= max_attempts
        if locked:
            await session.execute(
                update(User)
                .where(col(User.id) == user_id)
                .values(locked_until=now() + timedelta(minutes=lockout_minutes))
            )
        await session.commit()
        return int(attempts), locked


async def _load_user(session: DbSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(col(User.username) == username))
    ).scalar_one_or_none()


async def _roles_and_permissions(
    session: DbSession, user: User
) -> tuple[list[str], list[str], str]:
    """Read the user's effective access from the database.

    Read at login rather than trusted from the token, so a role change takes
    effect on the next login rather than whenever a token happens to expire.
    """
    from workbench.core.classification import Classification
    from workbench.db.models import UserRoleLink

    roles = list(
        (
            await session.execute(
                select(Role)
                .join(UserRoleLink, col(UserRoleLink.role_id) == Role.id)
                .where(col(UserRoleLink.user_id) == user.id)
            )
        ).scalars()
    )
    permissions = sorted({p.code for role in roles for p in role.permissions})
    clearance = Classification.PUBLIC
    for role in roles:
        if Classification.clearance_rank(role.clearance) > Classification.clearance_rank(clearance):
            clearance = role.clearance
    return sorted(r.name for r in roles), permissions, clearance


def _set_refresh_cookie(response: Response, token: str, days: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=days * 86400,
        httponly=True,  # unreachable from JavaScript, so XSS cannot steal it
        samesite="strict",  # not sent on cross-site requests
        secure=False,  # set true behind TLS; a plant deployment terminates TLS at the proxy
        path="/api/v1/auth",  # only ever sent to the endpoints that need it
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    audit: Audit,
) -> TokenResponse:
    settings = request.app.state.settings
    user = await _load_user(session, payload.username)
    client_ip = request.client.host if request.client else None

    # The same failure message and the same work either way: a distinguishable
    # response would enumerate valid usernames.
    if user is None or not user.is_active:
        if user is not None:
            await audit.deny(
                AuditAction.LOGIN_FAILED,
                reason="account disabled",
                actor_username=payload.username,
                actor_ip=client_ip,
            )
        else:
            await audit.deny(
                AuditAction.LOGIN_FAILED,
                reason="unknown username",
                actor_username=payload.username,
                actor_ip=client_ip,
            )
        raise AuthenticationError("Incorrect username or password.")

    if user.is_locked:
        await audit.deny(
            AuditAction.LOGIN_FAILED,
            reason="account locked",
            actor_user_id=user.id,
            actor_username=user.username,
            actor_ip=client_ip,
        )
        raise AccountLockedError()

    if not verify_password(payload.password, user.password_hash):
        # Persisted on its own connection: this request is about to raise, and
        # a counter that rolls back with it would never reach the lockout
        # threshold — the control would look present but never fire.
        attempts, locked = await _record_failed_attempt(
            user.id, settings.max_failed_logins, settings.lockout_minutes
        )
        if locked:
            log.warning("account_locked", username=user.username, attempts=attempts)
        await audit.deny(
            AuditAction.LOGIN_FAILED,
            reason=f"bad password (attempt {attempts}{' — account locked' if locked else ''})",
            actor_user_id=user.id,
            actor_username=user.username,
            actor_ip=client_ip,
            severity=Severity.CRITICAL if locked else Severity.WARNING,
        )
        raise AuthenticationError("Incorrect username or password.")

    # Upgrade the stored hash if the cost parameters have moved on.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now()

    roles, permissions, clearance = await _roles_and_permissions(session, user)
    user.clearance = clearance

    refresh = generate_refresh_token()
    db_session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh),
        expires_at=now() + timedelta(days=settings.refresh_token_days),
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(db_session)
    await session.flush()

    token, expires_in = request.app.state.tokens.issue_access_token(
        user_id=user.id,
        username=user.username,
        roles=roles,
        permissions=permissions,
        clearance=clearance,
        departments=list(user.departments),
        session_id=db_session.id,
    )
    _set_refresh_cookie(response, refresh, settings.refresh_token_days)

    await audit.log(
        AuditAction.LOGIN,
        actor_user_id=user.id,
        actor_username=user.username,
        actor_roles=roles,
        actor_ip=client_ip,
        session_id=db_session.id,
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        username=user.username,
        roles=roles,
        permissions=permissions,
        clearance=clearance,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request, response: Response, session: DbSession, audit: Audit
) -> TokenResponse:
    """Rotate a refresh token, revoking the chain if one is replayed."""
    settings = request.app.state.settings
    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        raise AuthenticationError("No refresh token was presented.")

    token_hash = hash_token(presented)
    stored = (
        await session.execute(select(Session).where(col(Session.refresh_token_hash) == token_hash))
    ).scalar_one_or_none()

    if stored is None:
        raise AuthenticationError("This session is no longer valid.")

    if stored.revoked_at is not None:
        # A token that was already rotated is being presented again. The only
        # ways that happens are a stolen cookie or a replay, so every session in
        # the chain is revoked rather than just this one.
        await _revoke_chain(session, stored)
        await audit.deny(
            AuditAction.TOKEN_REUSE_DETECTED,
            reason="a rotated refresh token was presented again; all sessions revoked",
            actor_user_id=stored.user_id,
            session_id=stored.id,
            severity=Severity.CRITICAL,
        )
        raise AuthenticationError("This session has been revoked for security reasons.")

    if not stored.is_valid:
        raise AuthenticationError("This session has expired.")

    user = (
        await session.execute(select(User).where(col(User.id) == stored.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("This account is no longer active.")

    stored.revoked_at = now()
    new_refresh = generate_refresh_token()
    rotated = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(new_refresh),
        expires_at=now() + timedelta(days=settings.refresh_token_days),
        rotated_from=stored.id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(rotated)
    await session.flush()

    roles, permissions, clearance = await _roles_and_permissions(session, user)
    token, expires_in = request.app.state.tokens.issue_access_token(
        user_id=user.id,
        username=user.username,
        roles=roles,
        permissions=permissions,
        clearance=clearance,
        departments=list(user.departments),
        session_id=rotated.id,
    )
    _set_refresh_cookie(response, new_refresh, settings.refresh_token_days)
    await audit.log(
        AuditAction.TOKEN_REFRESH,
        actor_user_id=user.id,
        actor_username=user.username,
        session_id=rotated.id,
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        username=user.username,
        roles=roles,
        permissions=permissions,
        clearance=clearance,
    )


async def _revoke_chain(session: DbSession, compromised: Session) -> None:
    """Revoke every session related to a replayed token."""
    related = (
        await session.execute(select(Session).where(col(Session.user_id) == compromised.user_id))
    ).scalars()
    for item in related:
        if item.revoked_at is None:
            item.revoked_at = now()


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: DbSession, audit: Audit
) -> dict[str, str]:
    presented = request.cookies.get(REFRESH_COOKIE)
    if presented:
        stored = (
            await session.execute(
                select(Session).where(col(Session.refresh_token_hash) == hash_token(presented))
            )
        ).scalar_one_or_none()
        if stored and stored.revoked_at is None:
            stored.revoked_at = now()
            await audit.log(AuditAction.LOGOUT, actor_user_id=stored.user_id, session_id=stored.id)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return {"status": "logged out"}


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal, session: DbSession) -> MeResponse:
    user = (
        await session.execute(select(User).where(col(User.id) == principal.user_id))
    ).scalar_one_or_none()
    return MeResponse(
        user_id=principal.user_id,
        username=principal.username,
        full_name=user.full_name if user else "",
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        clearance=principal.clearance,
        departments=sorted(principal.departments),
    )
