"""Carrying out an approved action after the run that asked for it has gone.

An approver is a person with a day of their own. The run that requests a
document holds an open connection and waits three minutes; a decision that
only counts inside that window is a decision made under the wrong pressure,
and in practice it meant the requester had to sit and watch while somebody
else was found. Approving an hour later did nothing at all: the request was
marked approved and no document was ever produced.

So the approval carries what it needs. The tool's arguments are bound *before*
approval is asked for — they have to be, or the approver would be signing off
an action whose details were still undecided — which means the approval record
already holds everything required to run it.

Exactly-once is the property that matters here. Two callers can reach this: a
run still waiting when the decision lands, and the decision itself. Both go
through :func:`execute_approved`, which claims the approval with a conditional
update before doing any work — the row moves from "not executed" to "executed"
in one statement, and only the caller whose update matched proceeds. Losing
that race is the normal case, not an error: the loser reads the result the
winner stored.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlmodel import col

from workbench.artifacts.records import persist_artifact
from workbench.core.citation import Citation
from workbench.core.clock import now
from workbench.core.logging import get_logger
from workbench.db.models import Approval, ApprovalStatus
from workbench.db.session import session_scope
from workbench.security.identity import display_name, principal_for
from workbench.security.rbac import Principal
from workbench.tools.base import ToolContext

log = get_logger(__name__)


#: How long a caller that lost the claim waits for the winner's result.
#: Generation itself takes milliseconds — the slow part, binding the
#: arguments, happened before approval was even asked for — so this is a
#: bound on the pathological case, not the normal one.
_RESULT_WAIT_S = 30.0
_RESULT_POLL_S = 0.1


async def _await_winner(approval_id: str) -> dict[str, Any]:
    """Wait for whoever claimed the work to record what it produced.

    Returning immediately was wrong in a way that undid the point of the
    claim. A loser got an empty result, and an empty result means "nothing to
    do here" to the caller — so the waiting run fell through and generated the
    document a second time. The lock held; the caller walked around it.

    A loser therefore waits, and if the winner never records anything it still
    returns something *truthy*, so no caller can read the failure as an
    invitation to do the work itself.
    """
    deadline = time.monotonic() + _RESULT_WAIT_S
    while time.monotonic() < deadline:
        async with session_scope() as session:
            other = await session.get(Approval, approval_id)
            if other is not None and other.execution_result:
                return dict(other.execution_result)
        await asyncio.sleep(_RESULT_POLL_S)

    log.warning("deferred_execution_result_never_arrived", approval_id=approval_id)
    return {
        "ok": False,
        "error": "the approved action is already being carried out elsewhere",
    }


def to_storable(run_context: dict[str, Any]) -> dict[str, Any]:
    """Make a run context safe to put in a JSONB column.

    The context carries Citation dataclasses, which is right for the builders
    that consume it and wrong for a database column — the insert fails with
    "Object of type Citation is not JSON serializable", and it fails at the
    moment the approval is requested, so the whole run dies rather than the
    document.
    """
    stored = dict(run_context)
    stored["citations"] = [c.as_dict() for c in run_context.get("citations") or []]
    return stored


def from_storable(stored: dict[str, Any]) -> dict[str, Any]:
    """The inverse: rebuild what the artifact builders expect."""
    context = dict(stored)
    context["citations"] = [
        Citation.from_dict(c) for c in stored.get("citations") or [] if isinstance(c, dict)
    ]
    return context


async def _requester_principal(user_id: str) -> Principal | None:
    """The principal whose authority this action runs under.

    Not the approver's. The two permissions are deliberately held by different
    people: the requester may generate documents, the approver may authorise
    one, and the approver's account cannot generate at all. Dispatching as the
    approver failed with "requires artifact:generate" — correctly, and giving
    them that right would collapse the separation this gate exists to create.
    """
    async with session_scope() as session:
        return await principal_for(session, user_id)


async def execute_approved(
    approval_id: str,
    *,
    tools: Any,
    principal: Principal,
) -> dict[str, Any]:
    """Run the action an approval authorised, at most once.

    Args:
        approval_id: The approval to act on. Must already be approved.
        tools: The tool registry to dispatch through.
        principal: Whoever is driving this call — the approver deciding, or
            the requester's run picking the result up. Used only for the tool
            context; the *authority* comes from the approval record, which a
            second person granted.

    Returns:
        The tool's result summary, or ``{}`` when there is nothing to do.
    """
    async with session_scope() as session:
        approval = await session.get(Approval, approval_id)
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            return {}
        if not approval.deferred:
            # Approvals recorded before this existed, and kinds that carry no
            # action of their own.
            return {}
        if approval.executed_at is not None:
            # Claimed already. If the result is there, take it; if the claim
            # was only just made, wait for it rather than returning nothing —
            # "nothing" reads as "no work to do" and sends the caller off to
            # do the work itself, which is the duplicate the claim exists to
            # prevent.
            if approval.execution_result:
                return dict(approval.execution_result)
            return await _await_winner(approval_id)
        # Read everything needed now, inside the session. The instance is
        # detached once this block exits, and touching an attribute then is
        # either a lazy load with no connection or a stale value.
        deferred = dict(approval.deferred)
        step_id = approval.step_id or ""
        requested_by = approval.requested_by or ""
        # Captured here so the document can name who signed it off. The run
        # context was built before the decision existed, so left alone the
        # provenance page reads "approved_by: null" on a document that was
        # very much approved — the one fact the page exists to carry.
        decided_by = approval.decided_by or ""
        decided_at = approval.decided_at.isoformat() if approval.decided_at else None
        conversation_id = str((approval.deferred or {}).get("conversation_id") or "")

    # Claim it. The condition is the claim: whoever's UPDATE matches a row
    # still marked unexecuted owns the work, and everyone else reads the
    # result. Checking and then writing would leave a window in which two
    # callers both believe they are first.
    async with session_scope() as session:
        # session.execute is typed as returning Result; an UPDATE always
        # yields a CursorResult, which is the one that carries rowcount.
        claimed = cast(
            "CursorResult[Any]",
            await session.execute(
                update(Approval)
                .where(
                    col(Approval.id) == approval_id,
                    col(Approval.executed_at).is_(None),
                    col(Approval.status) == ApprovalStatus.APPROVED,
                )
                .values(executed_at=now())
            ),
        )
        if claimed.rowcount == 0:
            return await _await_winner(approval_id)

    tool_name = str(deferred.get("tool") or "")

    # Runs as the requester. `principal` is whoever triggered this call — the
    # approver, usually — and is kept only so the audit shows who set it off.
    actor = await _requester_principal(requested_by) if requested_by else None
    if actor is None:
        unavailable: dict[str, Any] = {
            "ok": False,
            "tool": tool_name,
            "error": (
                "the account that requested this can no longer perform it, so the "
                "approval was not carried out"
            ),
        }
        async with session_scope() as session:
            stale = await session.get(Approval, approval_id)
            if stale is not None:
                stale.execution_result = unavailable
        log.warning(
            "deferred_execution_requester_unavailable",
            approval_id=approval_id,
            requested_by=requested_by,
        )
        return unavailable

    run_context = from_storable(dict(deferred.get("run_context") or {}))
    if decided_by:
        # The same form the live path writes, so one document does not name
        # its approver differently from the next depending on whether the
        # requester happened to still be connected.
        async with session_scope() as session:
            run_context["approved_by"] = await display_name(session, decided_by)
        run_context["approved_at"] = decided_at

    context = ToolContext(
        principal=actor,
        run_id=str(deferred.get("run_id") or approval_id),
        step_id=step_id,
        run_context=run_context,
    )

    try:
        result = await tools.dispatch(tool_name, dict(deferred.get("args") or {}), context)
        summary: dict[str, Any] = dict(result.summary())
        summary["artifacts"] = list(result.artifacts or [])

        # Recorded here too. The chat stream writes these rows as it sees the
        # events, and there is no stream any more — without this the file sits
        # in the store with no row, absent from the artifacts list and
        # undownloadable, while the approval cheerfully reports success.
        for artifact in summary["artifacts"]:
            await persist_artifact(
                artifact,
                run_id=str(deferred.get("run_id") or approval_id),
                conversation_id=conversation_id,
                created_by=requested_by,
                approved=True,
            )
    except Exception as exc:
        # The claim stands. Retrying on the next approve would need the
        # approval to be undecided again, and it is not — recording the
        # failure is what lets someone see why nothing appeared, rather than
        # silently producing nothing twice.
        log.error(
            "deferred_execution_failed", approval_id=approval_id, tool=tool_name, error=str(exc)
        )
        summary = {"ok": False, "tool": tool_name, "error": str(exc)[:300]}

    async with session_scope() as session:
        approval = await session.get(Approval, approval_id)
        if approval is not None:
            approval.execution_result = summary

    log.info(
        "deferred_execution_complete",
        approval_id=approval_id,
        tool=tool_name,
        ok=bool(summary.get("ok", True)),
    )
    return summary
