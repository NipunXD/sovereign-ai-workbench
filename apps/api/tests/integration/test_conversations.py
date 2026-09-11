"""Saved conversations belong to the person who had them.

The property under test is ownership. What someone asked the workbench is
theirs: an admin cannot read it, an auditor cannot read it, and another user
asking for it by id is told it does not exist — not that it is forbidden,
which would confirm there is something there.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlmodel import col

from workbench.api.v1.runs import persist_message
from workbench.core.errors import NotFoundError
from workbench.core.ids import set_deterministic
from workbench.db.models import Conversation, Message, MessageCitation, User
from workbench.db.session import dispose_engine, init_engine, session_scope
from workbench.security.rbac import Principal
from workbench.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def users():
    set_deterministic(False)
    init_engine(get_settings())
    try:
        async with session_scope() as session:
            rows = list((await session.execute(select(User).limit(3))).scalars())
    except Exception as exc:
        await dispose_engine()
        pytest.skip(f"database unavailable: {exc}")
    if len(rows) < 2:
        await dispose_engine()
        pytest.skip("need two seeded users")

    def principal(user: User) -> Principal:
        return Principal(
            user_id=user.id,
            username=user.username,
            roles=frozenset({"engineer"}),
            permissions=frozenset({"chat:use"}),
            clearance="internal",
        )

    created: list[str] = []

    async def conversation_for(user: User, title: str) -> str:
        async with session_scope() as session:
            conversation = Conversation(user_id=user.id, title=title)
            session.add(conversation)
            await session.flush()
            created.append(conversation.id)
            return conversation.id

    yield principal(rows[0]), principal(rows[1]), conversation_for

    async with session_scope() as session:
        message_ids = [
            m.id
            for m in (
                await session.execute(
                    select(Message).where(col(Message.conversation_id).in_(created))
                )
            ).scalars()
        ]
        if message_ids:
            await session.execute(
                delete(MessageCitation).where(col(MessageCitation.message_id).in_(message_ids))
            )
        await session.execute(delete(Message).where(col(Message.conversation_id).in_(created)))
        await session.execute(delete(Conversation).where(col(Conversation.id).in_(created)))
    await dispose_engine()
    set_deterministic(True)


async def test_a_conversation_is_invisible_to_everyone_but_its_owner(users) -> None:
    from workbench.api.v1.conversations import _owned

    alice, bob, conversation_for = users
    theirs = await conversation_for(await _user(alice), "V-1201 survey")

    async with session_scope() as session:
        assert (await _owned(session, theirs, alice)).id == theirs
        # Not forbidden: not found. The response must not confirm existence.
        with pytest.raises(NotFoundError):
            await _owned(session, theirs, bob)


async def test_the_list_only_ever_contains_your_own(users) -> None:
    from workbench.api.v1.conversations import list_conversations

    alice, bob, conversation_for = users
    mine = await conversation_for(await _user(alice), "mine")
    theirs = await conversation_for(await _user(bob), "mine")
    # Only sessions with a stored turn are listed; an empty one is a dead link.
    await persist_message(conversation_id=mine, role="user", content="hello")
    await persist_message(conversation_id=theirs, role="user", content="hello")

    # Searched by title rather than taken from the top of the list: the test
    # process runs on a frozen clock, so every row it creates ties on
    # updated_at and position in the list means nothing.
    async with session_scope() as session:
        as_alice = {c.id for c in await list_conversations(session, alice, "mine", 500)}
        as_bob = {c.id for c in await list_conversations(session, bob, "mine", 500)}
    assert mine in as_alice
    assert theirs not in as_alice
    assert mine not in as_bob


async def test_an_archived_conversation_is_gone_from_its_owner_too(users) -> None:
    from workbench.api.v1.conversations import _owned, archive_conversation

    alice, _bob, conversation_for = users
    conversation_id = await conversation_for(await _user(alice), "old")

    async with session_scope() as session:
        await archive_conversation(conversation_id, session, alice)
    async with session_scope() as session:
        with pytest.raises(NotFoundError):
            await _owned(session, conversation_id, alice)


async def test_turns_and_citations_come_back_in_order(users) -> None:
    """Both halves of a saved exchange, with the citations the answer carried."""
    from workbench.api.v1.conversations import get_conversation

    alice, _bob, conversation_for = users
    conversation_id = await conversation_for(await _user(alice), "readings")
    await persist_message(
        conversation_id=conversation_id, role="user", content="What is CML-04 at?"
    )
    await persist_message(
        conversation_id=conversation_id,
        role="assistant",
        content="CML-04 measured 9.20 mm [1].",
        run_id="run_x",
        model_used="qwen/qwen3-8b",
        latency_ms=1200,
        citations=[
            {
                "n": 1,
                "chunk_id": "chk_1",
                "doc_id": "doc_1",
                "doc_title": "Inspection Report V-1201",
                "page_no": 2,
                "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.3},
                "snippet": "CML-04 measured 9.20 mm",
                "section_path": ["4. Findings"],
                "score": 0.03,
                "retrieval_method": "hybrid",
                "confidence": 0.61,
            }
        ],
    )

    async with session_scope() as session:
        detail = await get_conversation(conversation_id, session, alice)

    assert [m.role for m in detail.messages] == ["user", "assistant"]
    answer = detail.messages[1]
    assert answer.content == "CML-04 measured 9.20 mm [1]."
    assert answer.model_used == "qwen/qwen3-8b"
    assert answer.citations[0].doc_title == "Inspection Report V-1201"
    assert answer.citations[0].page_no == 2
    # The OCR confidence survives the round trip — it is what marks a figure
    # read from a bad photocopy, and losing it would drop the warning.
    assert answer.citations[0].confidence == pytest.approx(0.61)


async def _user(principal: Principal) -> User:
    async with session_scope() as session:
        user = await session.get(User, principal.user_id)
        assert user is not None
        return user
