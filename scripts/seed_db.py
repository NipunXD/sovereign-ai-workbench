#!/usr/bin/env python3
"""Seed roles, permissions and the demo user accounts.

Idempotent: running it twice changes nothing. Roles and permissions come from
config/rbac.yaml so there is one definition of the access model rather than a
config file and a divergent copy in the database.

The demo accounts exist to make the separation-of-duties story checkable in the
UI — an engineer who generates a report cannot approve it, and an auditor can
see every action without being able to take one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sqlalchemy import select  # noqa: E402

from workbench.core.logging import configure_logging, get_logger  # noqa: E402
from workbench.db.models import (  # noqa: E402
    Permission,
    Role,
    RolePermissionLink,
    User,
    UserRoleLink,
)
from workbench.db.session import init_engine, session_scope  # noqa: E402
from workbench.security.auth import hash_password  # noqa: E402
from workbench.security.rbac import RbacConfig  # noqa: E402
from workbench.settings import get_settings  # noqa: E402

log = get_logger("seed")

#: username -> (full name, roles, departments). Deliberately spans the
#: separation-of-duties boundary so the approval gate can be demonstrated with
#: two browsers rather than described.
DEMO_USERS: dict[str, tuple[str, list[str], list[str]]] = {
    "admin": ("Platform Administrator", ["admin"], []),
    "engineer": ("R. Krishnan (Inspection)", ["engineer"], ["inspection"]),
    "senior": ("S. Nair (Senior Inspection)", ["senior_engineer"], ["inspection", "process"]),
    "approver": ("M. Devadiga (Maintenance Head)", ["approver"], ["inspection", "maintenance"]),
    "auditor": ("Internal Audit", ["auditor"], []),
    "viewer": ("Plant Operator", ["viewer"], ["operations"]),
}


async def seed() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    init_engine(settings)

    rbac = RbacConfig.load(settings.rbac_config)
    password = settings.seed_password.get_secret_value()

    async with session_scope() as session:
        # --- permissions ---
        existing_permissions = {
            code for (code,) in (await session.execute(select(Permission.code))).all()
        }
        for code, description in rbac.permissions.items():
            if code not in existing_permissions:
                session.add(Permission(code=code, description=description))
        await session.flush()

        permissions_by_code = {
            p.code: p for p in (await session.execute(select(Permission))).scalars()
        }

        # --- roles ---
        existing_roles = {r.name: r for r in (await session.execute(select(Role))).scalars()}
        for name, spec in rbac.roles.items():
            role = existing_roles.get(name)
            if role is None:
                role = Role(
                    name=name,
                    description=str(spec.get("description", "")),
                    clearance=str(spec.get("clearance", "public")),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
                existing_roles[name] = role

            # Link rows are inserted directly rather than by assigning
            # role.permissions. Assigning a relationship collection makes
            # SQLAlchemy load the current one to compute a delta, and under
            # asyncio that load happens outside the greenlet and raises.
            granted = rbac.permissions_for({name})
            existing_links = {
                permission_id
                for (permission_id,) in (
                    await session.execute(
                        select(RolePermissionLink.permission_id).where(
                            RolePermissionLink.role_id == role.id
                        )
                    )
                ).all()
            }
            for code in sorted(granted):
                permission = permissions_by_code.get(code)
                if permission is not None and permission.id not in existing_links:
                    session.add(RolePermissionLink(role_id=role.id, permission_id=permission.id))
        await session.flush()

        # --- users ---
        existing_users = {u.username: u for u in (await session.execute(select(User))).scalars()}
        created = []
        for username, (full_name, role_names, departments) in DEMO_USERS.items():
            if username in existing_users:
                continue
            user = User(
                username=username,
                full_name=full_name,
                email=f"{username}@mrpl.local",
                password_hash=hash_password(password),
                clearance=rbac.clearance_for(set(role_names)),
                departments=departments,
                # Demo accounts, so no forced rotation; a real deployment would
                # set this and the first login would require a change.
                must_change_password=False,
            )
            session.add(user)
            await session.flush()
            for role_name in role_names:
                if role := existing_roles.get(role_name):
                    session.add(UserRoleLink(user_id=user.id, role_id=role.id))
            created.append(username)

    print(f"permissions: {len(rbac.permissions)}   roles: {len(rbac.roles)}")
    if created:
        print(f"created users: {', '.join(created)}  (password: {password})")
    else:
        print("users already present; nothing to do")

    print()
    print(f"{'username':<12} {'clearance':<14} {'roles':<18} departments")
    print("-" * 72)
    for username, (_, roles, departments) in DEMO_USERS.items():
        clearance = rbac.clearance_for(set(roles))
        print(
            f"{username:<12} {clearance:<14} {','.join(roles):<18} {','.join(departments) or '-'}"
        )


if __name__ == "__main__":
    asyncio.run(seed())
