"""The approval gate.

Separation of duties is the property under test: approval means a *second*
person looked. Enforced in the gate rather than only in the UI, because a user
who holds both permissions would otherwise be able to sign off their own work.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from workbench.agent.approval import ApprovalGate, ApprovalRequest
from workbench.core.errors import AuthorizationError, ConflictError, NotFoundError
from workbench.core.ids import set_deterministic
from workbench.db.models import ApprovalStatus, User
from workbench.db.session import dispose_engine, get_session_factory, init_engine, session_scope
from workbench.security.rbac import Principal, RbacConfig
from workbench.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def gate(rbac_config_path):
    # Real ULIDs: the suite freezes identifiers for golden files, which collides
    # in a persistent database.
    set_deterministic(False)
    settings = get_settings()
    init_engine(settings)
    try:
        async with session_scope() as session:
            await session.execute(select(User).limit(1))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        pytest.skip(f"database unavailable: {exc}")

    rbac = RbacConfig.load(rbac_config_path)

    async def principal(username: str, role: str) -> Principal:
        async with session_scope() as session:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
        if user is None:
            pytest.skip(f"demo user '{username}' not seeded")
        return Principal(
            user_id=user.id,
            username=username,
            roles=frozenset({role}),
            permissions=rbac.permissions_for({role}),
            clearance=rbac.clearance_for({role}),
        )

    yield ApprovalGate(get_session_factory()), principal
    await dispose_engine()
    set_deterministic(True)


async def test_a_second_person_can_approve(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")

    approval = await approvals.request(
        run_id="run_test_ok",
        step_id="s1",
        principal=requester,
        request=ApprovalRequest(
            kind="artifact", subject_type="artifact", subject_id="sha_ok",
            summary={"tool": "artifact.docx"},
        ),
    )
    assert approval.status == ApprovalStatus.PENDING

    decided = await approvals.decide(
        approval.id, approver=approver, approved=True, comment="checked"
    )
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_by == approver.user_id
    assert decided.comment == "checked"


async def test_the_requester_cannot_approve_their_own_request(gate) -> None:
    """The property the whole gate exists for.

    Enforced here rather than only by hiding a button, because a user holding
    both permissions would otherwise sign off their own work.
    """
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")

    approval = await approvals.request(
        run_id="run_test_self",
        step_id="s1",
        principal=requester,
        request=ApprovalRequest(
            kind="artifact", subject_type="artifact", subject_id="sha_self", summary={},
        ),
    )
    with pytest.raises(AuthorizationError, match="your own request"):
        await approvals.decide(approval.id, approver=requester, approved=True)

    still = await approvals.status(approval.id)
    assert still is not None and still.status == ApprovalStatus.PENDING


async def test_a_decision_cannot_be_revisited(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")

    approval = await approvals.request(
        run_id="run_test_twice",
        step_id="s1",
        principal=requester,
        request=ApprovalRequest(
            kind="tool", subject_type="tool", subject_id="code.run_python", summary={},
        ),
    )
    await approvals.decide(approval.id, approver=approver, approved=False, comment="no")

    with pytest.raises(ConflictError, match="already"):
        await approvals.decide(approval.id, approver=approver, approved=True)


async def test_an_unknown_request_is_not_found(gate) -> None:
    approvals, _ = gate
    with pytest.raises(NotFoundError):
        await approvals.decide("apr_does_not_exist", approver=None, approved=True)  # type: ignore[arg-type]


async def test_a_stale_request_expires_rather_than_waiting_forever(gate) -> None:
    """A run must always terminate.

    A gate that can wait indefinitely becomes a queue of half-finished work
    nobody can see, so an undecided request resolves as a refusal — the safe
    direction.
    """
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")

    approval = await approvals.request(
        run_id="run_test_expiry",
        step_id="s1",
        principal=requester,
        request=ApprovalRequest(
            kind="tool", subject_type="tool", subject_id="artifact.xlsx", summary={},
        ),
    )

    from datetime import timedelta

    from workbench.core.clock import now
    from workbench.db.models import Approval

    async with session_scope() as session:
        row = (
            await session.execute(select(Approval).where(Approval.id == approval.id))
        ).scalar_one()
        row.expires_at = now() - timedelta(hours=1)

    expired = await approvals.expire_stale()
    assert expired >= 1

    final = await approvals.status(approval.id)
    assert final is not None and final.status == ApprovalStatus.EXPIRED


async def test_requesting_and_deciding_are_both_audited(gate) -> None:
    """A decision nobody can trace is not an approval."""
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")

    approval = await approvals.request(
        run_id="run_test_audit",
        step_id="s1",
        principal=requester,
        request=ApprovalRequest(
            kind="artifact", subject_type="artifact", subject_id="sha_audit", summary={},
        ),
    )
    await approvals.decide(approval.id, approver=approver, approved=True, comment="fine")

    from workbench.db.models import AuditEvent

    async with session_scope() as session:
        events = list(
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.resource_id == approval.id)
                )
            ).scalars()
        )
    actions = {event.action for event in events}
    assert "approval.request" in actions
    assert "approval.decide" in actions
    decision = next(e for e in events if e.action == "approval.decide")
    assert decision.actor_username == "approver"
