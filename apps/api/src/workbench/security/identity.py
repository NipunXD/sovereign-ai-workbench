"""Reading a user's effective access from the database.

Lives here rather than in the login route because two callers need it and they
sit in different layers. Login reads it to mint a token. A deferred approval
reads it to run an approved action under the authority of whoever requested it
— which is not whoever approved it, deliberately: the approver's account
cannot generate documents, and dispatching as them fails with "requires
artifact:generate", correctly.

Read from the database each time rather than carried along. An approval
granted last night must not resurrect an account that lost the permission this
morning, and the same argument applies at login: a role change should take
effect on the next read, not whenever a token happens to expire.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from workbench.core.classification import Classification
from workbench.db.models import Role, User, UserRoleLink
from workbench.security.rbac import Principal


async def roles_and_permissions(
    session: AsyncSession, user: User
) -> tuple[list[str], list[str], str]:
    """The user's role names, permission codes and clearance."""
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


async def principal_for(session: AsyncSession, user_id: str) -> Principal | None:
    """Build a principal for a user id, or None if they cannot act.

    An inactive account returns None rather than a principal with no
    permissions: the two are different outcomes and only one of them deserves
    the message "this account can no longer perform it".
    """
    user = (await session.execute(select(User).where(col(User.id) == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    roles, permissions, clearance = await roles_and_permissions(session, user)
    return Principal(
        user_id=user.id,
        username=user.username,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        clearance=clearance,
        departments=frozenset(user.departments or []),
    )


async def display_name(session: AsyncSession, user_id: str | None) -> str | None:
    """How a person should be named in a document.

    Their full name where there is one, falling back to the username and then
    the id. A user id on a provenance page is not a record of who approved
    something — nobody reading the document can look it up.
    """
    if not user_id:
        return None
    user = (await session.execute(select(User).where(col(User.id) == user_id))).scalar_one_or_none()
    if user is None:
        return user_id
    return user.full_name or user.username
