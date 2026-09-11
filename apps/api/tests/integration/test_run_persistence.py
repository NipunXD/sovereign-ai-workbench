"""Run lifecycle records.

`agent_runs` existed from the first migration and nothing ever wrote to it.
That stayed invisible while runs completed — the trace streams live, so nobody
missed the row — until a stream was cancelled two minutes in and there was no
way to answer whether the run was still working or had stopped. The browser
showed a spinner; the run had ended; nothing in the system knew either.

The case that matters most here is the cancelled one, because that is the one
that was silently lost: the closing write went through the request's own
session, which the cancellation was already tearing down.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import select

from workbench.api.v1.runs import close_run, open_run, reap_orphans
from workbench.core.clock import now
from workbench.core.ids import prefixed_id, set_deterministic
from workbench.db.models import AgentRun, RunStatus, User
from workbench.db.session import dispose_engine, init_engine, session_scope
from workbench.security.rbac import Principal
from workbench.settings import get_settings

pytestmark = pytest.mark.integration

#: Conversation ids the tests below create, removed in the fixture teardown.
#: They ran against the shared dev database and every one left behind is a
#: row in a real person's saved-sessions list.
CREATED: list[str] = []


def _conversation_id() -> str:
    conversation_id = prefixed_id("conversation")
    CREATED.append(conversation_id)
    return conversation_id


@pytest.fixture
async def db_user():
    set_deterministic(False)
    settings = get_settings()
    init_engine(settings)
    try:
        async with session_scope() as session:
            user = (await session.execute(select(User).limit(1))).scalars().first()
            if user is None:
                pytest.skip("no seeded users — run `make seed`")
            principal = Principal(
                user_id=user.id,
                username=user.username,
                roles=frozenset({"engineer"}),
                permissions=frozenset({"chat:use"}),
                clearance="internal",
            )
    except Exception as exc:
        await dispose_engine()
        pytest.skip(f"database unavailable: {exc}")

    yield principal

    if CREATED:
        from sqlalchemy import delete
        from sqlmodel import col

        from workbench.db.models import AgentRun, Conversation, Message, MessageCitation

        async with session_scope() as session:
            message_ids = [
                m.id
                for m in (
                    await session.execute(
                        select(Message).where(col(Message.conversation_id).in_(CREATED))
                    )
                ).scalars()
            ]
            if message_ids:
                await session.execute(
                    delete(MessageCitation).where(col(MessageCitation.message_id).in_(message_ids))
                )
            await session.execute(delete(Message).where(col(Message.conversation_id).in_(CREATED)))
            await session.execute(
                delete(AgentRun).where(col(AgentRun.conversation_id).in_(CREATED))
            )
            await session.execute(delete(Conversation).where(col(Conversation.id).in_(CREATED)))
        CREATED.clear()

    # Disposed between tests, as the other integration suites do. An engine
    # left open leaks connections that surface as unraisable exceptions from
    # the garbage collector rather than as a clear failure.
    await dispose_engine()
    set_deterministic(True)


async def _fetch(run_id: str) -> AgentRun | None:
    async with session_scope() as session:
        return await session.get(AgentRun, run_id)


async def test_a_run_is_recorded_when_it_starts(db_user) -> None:
    run_id = prefixed_id("run")
    await open_run(
        run_id=run_id,
        conversation_id=_conversation_id(),
        principal=db_user,
        message="What is the depressurisation rate limit for V-1201?",
    )
    run = await _fetch(run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.finished_at is None
    assert "V-1201" in run.input


async def test_a_completed_run_is_closed_with_its_plan(db_user) -> None:
    run_id = prefixed_id("run")
    await open_run(
        run_id=run_id,
        conversation_id=_conversation_id(),
        principal=db_user,
        message="anything",
    )
    await close_run(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        plan={"steps": [{"id": "1", "intent": "retrieve"}]},
        budget={"tool_calls_used": 0},
        validation={"grounded_ratio": 1.0},
    )
    run = await _fetch(run_id)
    assert run is not None
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.wall_ms is not None and run.wall_ms >= 0
    assert run.plan["steps"][0]["intent"] == "retrieve"
    assert run.validation["grounded_ratio"] == 1.0


async def test_a_cancelled_run_is_recorded_as_cancelled(db_user) -> None:
    """The case that was being lost.

    A client that disappears mid-run must leave a run that says so, rather than
    one stuck on `running` with nothing left alive to close it.
    """
    run_id = prefixed_id("run")
    await open_run(
        run_id=run_id,
        conversation_id=_conversation_id(),
        principal=db_user,
        message="Produce a Word report.",
    )
    await close_run(
        run_id=run_id,
        status=RunStatus.CANCELLED,
        error={"reason": "the client disconnected before the run finished"},
    )
    run = await _fetch(run_id)
    assert run is not None
    assert run.status == RunStatus.CANCELLED
    assert run.finished_at is not None
    assert run.error is not None and "disconnected" in run.error["reason"]


async def test_closing_an_unknown_run_is_not_an_error(db_user) -> None:
    # close_run() is called from a `finally`; raising there would replace the
    # real failure with a less useful one.
    await close_run(run_id="run_does_not_exist", status=RunStatus.FAILED)


async def test_a_second_run_reuses_its_conversation(db_user) -> None:
    conversation_id = _conversation_id()
    first, second = prefixed_id("run"), prefixed_id("run")
    await open_run(run_id=first, conversation_id=conversation_id, principal=db_user, message="one")
    await open_run(run_id=second, conversation_id=conversation_id, principal=db_user, message="two")
    assert (await _fetch(first)) is not None
    assert (await _fetch(second)) is not None


async def test_startup_reaps_runs_a_dead_process_left_behind(db_user) -> None:
    """A run only ends when its own request ends.

    A killed or restarted API leaves rows marked `running` that nothing will
    ever close, so startup treats anything still running from a previous
    process as what it is.
    """
    run_id = prefixed_id("run")
    await open_run(
        run_id=run_id,
        conversation_id=_conversation_id(),
        principal=db_user,
        message="orphan",
    )
    # Backdate it past the reaper's window.
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.started_at = now().replace(year=now().year - 1)

    assert await reap_orphans(older_than_s=60.0) >= 1

    reaped = await _fetch(run_id)
    assert reaped is not None
    assert reaped.status == RunStatus.CANCELLED
    assert reaped.finished_at is not None


async def test_a_recent_run_is_not_reaped(db_user) -> None:
    """The reaper must not kill a run that is genuinely in flight."""
    run_id = prefixed_id("run")
    await open_run(
        run_id=run_id,
        conversation_id=_conversation_id(),
        principal=db_user,
        message="still going",
    )
    await reap_orphans(older_than_s=900.0)
    run = await _fetch(run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING


async def test_finalisation_survives_the_cancellation_that_triggered_it(db_user) -> None:
    """The bug this whole path exists for.

    A disconnected client cancels the streaming task, and Starlette runs that
    task inside an anyio cancel scope. Inside a *cancelled* scope every await
    re-raises CancelledError — not just the first one, which is how ordinary
    asyncio cleanup gets to run — so closing the run directly in the `finally`
    executed nothing at all. The cancellation was logged and the run stayed
    marked `running` for good, which is exactly what was found in the database
    afterwards.

    The scope here is what gives this test teeth: with a plain `task.cancel()`
    the inline version passes, because raw asyncio delivers the cancellation
    once and lets the `finally` proceed. Only the scope reproduces it.
    """
    import anyio

    from workbench.api.v1.chat import _finalise_in_background

    inline_ran: list[str] = []

    async def run_once(*, detached: bool) -> str:
        run_id = prefixed_id("run")
        conversation_id = _conversation_id()
        await open_run(
            run_id=run_id,
            conversation_id=conversation_id,
            principal=db_user,
            message="Produce a Word report.",
        )

        async def streaming_task() -> None:
            try:
                await anyio.sleep(30)  # stands in for the agent working
            finally:
                if detached:
                    _finalise_in_background(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        status=RunStatus.CANCELLED,
                        plan={},
                        budget={},
                        validation={},
                        failure={"reason": "the client disconnected"},
                        citations=0,
                        principal=db_user,
                    )
                else:
                    # Deliberately not the real close_run: cancelling it
                    # mid-session strands a pooled connection, and the leak
                    # would be this test's doing rather than the code's. All
                    # that needs proving is that the await does not run.
                    with contextlib.suppress(BaseException):
                        await anyio.lowlevel.checkpoint()
                        inline_ran.append(run_id)

        async with anyio.create_task_group() as tg:
            tg.start_soon(streaming_task)
            await anyio.sleep(0.1)
            tg.cancel_scope.cancel()

        for _ in range(40):
            await anyio.sleep(0.1)
            run = await _fetch(run_id)
            if run is not None and run.status == RunStatus.CANCELLED:
                break
        run = await _fetch(run_id)
        assert run is not None
        return run.status

    # The shape that was shipped: the cleanup never executes, so the run is
    # stranded on `running` exactly as it was found in the database.
    assert await run_once(detached=False) == RunStatus.RUNNING
    assert inline_ran == [], "the awaited cleanup should not have run"

    # The fix: the close-out survives the cancellation that caused it.
    assert await run_once(detached=True) == RunStatus.CANCELLED
