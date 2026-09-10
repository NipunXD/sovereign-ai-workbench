"""The audit chain, against a real database.

Tested against Postgres rather than a fake because two of the three guarantees
are enforced by the database itself: the append-only trigger and the sequence
that gives the chain its order. A fake would only prove the Python is
self-consistent, which is not the claim being made.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from workbench.core.ids import set_deterministic
from workbench.db.models import AuditAction
from workbench.db.session import dispose_engine, init_engine, session_scope
from workbench.security.audit import AuditLogger, verify_chain
from workbench.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    """A live database session, skipped when Postgres is not reachable."""
    # The suite freezes identifiers so golden files are stable, but these tests
    # insert into a persistent database where a counter restarting at 1 collides
    # with rows from the previous test. Real ULIDs for the duration.
    set_deterministic(False)
    settings = get_settings()
    init_engine(settings)
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await dispose_engine()
        pytest.skip(f"database unavailable: {exc}")
    yield
    await dispose_engine()
    set_deterministic(True)


async def test_chain_verifies_over_the_live_log(db: None) -> None:
    async with session_scope() as session:
        report = await verify_chain(session)
    assert report.valid, report.detail


async def test_appended_events_extend_the_chain(db: None) -> None:
    """A new record links to whatever came before it."""
    async with session_scope() as session:
        logger = AuditLogger(session)
        first = await logger.log(
            AuditAction.DOC_READ,
            actor_username="test-harness",
            resource_type="document",
            resource_id="doc_test",
        )
        second = await logger.log(
            AuditAction.RAG_SEARCH,
            actor_username="test-harness",
            metadata={"query": "test"},
        )
        assert second.prev_hash == first.hash
        assert second.seq is not None and first.seq is not None
        assert second.seq > first.seq

    async with session_scope() as session:
        assert (await verify_chain(session)).valid


async def test_the_database_refuses_to_update_an_audit_row(db: None) -> None:
    """The hash chain detects tampering; the trigger prevents it.

    Without this, anyone holding the application's own credentials could
    rewrite history and recompute the hashes to match.
    """
    async with session_scope() as session:
        logger = AuditLogger(session)
        event = await logger.log(AuditAction.DOC_READ, actor_username="test-harness")
        seq = event.seq

    with pytest.raises(Exception, match="append-only"):
        async with session_scope() as session:
            await session.execute(
                text("UPDATE audit_events SET reason = 'tampered' WHERE seq = :seq"),
                {"seq": seq},
            )


async def test_the_database_refuses_to_delete_an_audit_row(db: None) -> None:
    async with session_scope() as session:
        logger = AuditLogger(session)
        event = await logger.log(AuditAction.DOC_READ, actor_username="test-harness")
        seq = event.seq

    with pytest.raises(Exception, match="append-only"):
        async with session_scope() as session:
            await session.execute(
                text("DELETE FROM audit_events WHERE seq = :seq"), {"seq": seq}
            )


async def test_tampering_is_detected_at_the_exact_row(db: None) -> None:
    """Simulate an attacker with direct table access.

    The trigger stops the application path, so this disables it deliberately —
    the point is that even someone who can bypass the trigger cannot bypass the
    chain, and verification names the row they touched.
    """
    async with session_scope() as session:
        logger = AuditLogger(session)
        for index in range(3):
            await logger.log(AuditAction.DOC_READ, actor_username=f"chain-{index}")
        target = (await logger.log(AuditAction.DOC_READ, actor_username="victim")).seq
        await logger.log(AuditAction.DOC_READ, actor_username="after")

    async with session_scope() as session:
        await session.execute(
            text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable")
        )
        await session.execute(
            text("UPDATE audit_events SET reason = 'quietly altered' WHERE seq = :seq"),
            {"seq": target},
        )
        await session.execute(
            text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_immutable")
        )

    try:
        async with session_scope() as session:
            report = await verify_chain(session)
        assert not report.valid
        assert report.first_invalid_seq == target
        assert "modified" in report.detail
    finally:
        # Restore the row so the shared log verifies again for other tests.
        async with session_scope() as session:
            await session.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable")
            )
            await session.execute(
                text("UPDATE audit_events SET reason = NULL WHERE seq = :seq"),
                {"seq": target},
            )
            await session.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_immutable")
            )


async def test_denials_survive_the_rollback_that_follows_them(db: None) -> None:
    """A refused action raises, which rolls back the request transaction.

    A failed login that leaves no trace is worse than no audit log at all — it
    looks like nothing happened. Denials are written on their own connection
    for exactly this reason.
    """
    async with session_scope() as session:
        before = await verify_chain(session)

    async with session_scope() as session:
        logger = AuditLogger(session)
        await logger.deny(
            AuditAction.LOGIN_FAILED,
            reason="test harness denial",
            actor_username="nobody",
        )
        # Abandon the surrounding transaction, as a raising endpoint would.
        await session.rollback()

    async with session_scope() as session:
        after = await verify_chain(session)

    assert after.valid
    assert after.events_checked > before.events_checked, (
        "the denial was rolled back with the request transaction"
    )
