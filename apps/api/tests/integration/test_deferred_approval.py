"""Acting on an approval after the run that asked for it has gone.

An approver is a person with a day of their own. The requesting run holds an
open connection for three minutes; before this, a decision made after that
window did nothing at all — the approval was recorded and no document was ever
produced, which is the worst of both, since the record says it was approved.

The property under test is exactly-once. Two callers can reach the executor —
a run still waiting when the decision lands, and the decision itself — and a
document generated twice is as wrong as one generated never.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlmodel import col

from workbench.agent.approval import ApprovalGate, ApprovalRequest
from workbench.agent.deferred import execute_approved
from workbench.core.ids import set_deterministic
from workbench.db.models import Approval, ApprovalStatus, User
from workbench.db.session import dispose_engine, get_session_factory, init_engine, session_scope
from workbench.security.rbac import Principal, RbacConfig
from workbench.settings import get_settings

pytestmark = pytest.mark.integration


class _CountingTools:
    """A tool registry that records how often it was actually dispatched."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, name: str, args: dict[str, Any], ctx: Any) -> Any:
        self.calls.append((name, dict(args)))
        # Slow enough that two concurrent callers genuinely overlap.
        await asyncio.sleep(0.05)

        class _Result:
            artifacts: ClassVar[list[dict[str, str]]] = [
                {"filename": "report.docx", "sha256": "abc123"}
            ]

            def summary(self) -> dict[str, Any]:
                return {"ok": True, "tool": name}

        return _Result()


@pytest.fixture
async def gate(rbac_config_path):
    set_deterministic(False)
    settings = get_settings()
    init_engine(settings)
    try:
        async with session_scope() as session:
            await session.execute(select(User).limit(1))
    except Exception as exc:
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

    # Remove what these tests wrote. They ran against the shared dev database,
    # and pending rows left behind appear in a real approver's queue — and
    # get picked up by anything that approves "the newest pending request".
    from sqlalchemy import delete

    from workbench.db.models import Approval

    async with session_scope() as session:
        await session.execute(delete(Approval).where(col(Approval.run_id) == "run_deferred_test"))
    await dispose_engine()
    set_deterministic(True)


async def _request(gate: ApprovalGate, requester: Principal) -> Approval:
    return await gate.request(
        run_id="run_deferred_test",
        step_id="s2",
        principal=requester,
        request=ApprovalRequest(
            kind="tool",
            subject_type="tool",
            subject_id="artifact.docx",
            summary={"tool": "artifact.docx"},
            deferred={
                "tool": "artifact.docx",
                "args": {"title": "V-1201 Survey", "sections": []},
                "run_context": {"models": {}},
                "requested_by": requester.username,
            },
        ),
    )


async def test_an_approval_carries_what_it_authorises(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approval = await _request(approvals, requester)

    async with session_scope() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        # The arguments are bound before approval is asked for, so the
        # approver is signing off a decided action rather than an intention.
        assert stored.deferred["tool"] == "artifact.docx"
        assert stored.deferred["args"]["title"] == "V-1201 Survey"


async def test_the_action_runs_when_a_second_person_approves(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")
    approval = await _request(approvals, requester)
    await approvals.decide(approval.id, approver=approver, approved=True)

    tools = _CountingTools()
    result = await execute_approved(approval.id, tools=tools, principal=approver)

    assert result["ok"] is True
    assert len(tools.calls) == 1
    assert tools.calls[0][0] == "artifact.docx"


async def test_it_runs_exactly_once_however_many_callers_arrive(gate) -> None:
    """The run and the decision can both reach it, and often will.

    A document generated twice is as wrong as one generated never: two
    approval-stamped files for one decision, and whichever the reader opens is
    a coin toss.
    """
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")
    approval = await _request(approvals, requester)
    await approvals.decide(approval.id, approver=approver, approved=True)

    tools = _CountingTools()
    results = await asyncio.gather(
        *(execute_approved(approval.id, tools=tools, principal=approver) for _ in range(5))
    )

    assert len(tools.calls) == 1, f"dispatched {len(tools.calls)} times"
    # Every caller gets the same answer, including the four that lost.
    assert all(r.get("ok") is True for r in results)


async def test_a_rejected_approval_does_nothing(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")
    approval = await _request(approvals, requester)
    await approvals.decide(approval.id, approver=approver, approved=False, comment="no")

    tools = _CountingTools()
    assert await execute_approved(approval.id, tools=tools, principal=approver) == {}
    assert tools.calls == []


async def test_a_pending_approval_does_nothing(gate) -> None:
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approval = await _request(approvals, requester)

    tools = _CountingTools()
    assert await execute_approved(approval.id, tools=tools, principal=requester) == {}
    assert tools.calls == []


async def test_the_result_is_readable_afterwards(gate) -> None:
    """So a requester who reconnects can find out what happened."""
    approvals, principal = gate
    requester = await principal("senior", "senior_engineer")
    approver = await principal("approver", "approver")
    approval = await _request(approvals, requester)
    await approvals.decide(approval.id, approver=approver, approved=True)
    await execute_approved(approval.id, tools=_CountingTools(), principal=approver)

    async with session_scope() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        assert stored.executed_at is not None
        assert stored.execution_result["ok"] is True
        assert stored.status == ApprovalStatus.APPROVED
