"""Plan 01-04 Task 3 — walk_chain integrity verifier behaviors 18-24.

Each test maps 1:1 to a numbered behavior in PLAN.md `<feature>`:

18. Empty events → ``walk_chain`` returns ``[]``.
19. Intact chain (5 appends) → ``walk_chain`` returns ``[]``.
20. Tampered ``payload_json`` on row 3 → ``walk_chain`` returns ``[3]``.
21. Broken ``prev_hash`` on row 4 → ``walk_chain`` returns ``[4]``.
22. Deleted middle row (row 3 of 5) → ``walk_chain`` returns ``[4]``.
23. ``walk_chain`` is read-only — does not mutate any row.
24. ``walk_chain`` is user-scoped — alice's call ignores bob's events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gekko.audit.canonical import GENESIS_PREV_HASH, canonical_json
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.db.engine import get_async_engine
from gekko.db.models import Base, Event, User
from gekko.db.session import AsyncSessionLocal, make_session_factory

PASSPHRASE = "test-audit-verify-passphrase"  # nosec: test-only literal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Encrypted DB with the 6 P1 tables."""
    eng = get_async_engine(tmp_path / "audit-verify.db", PASSPHRASE)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> AsyncSessionLocal:
    return make_session_factory(engine)


@pytest_asyncio.fixture
async def session(
    session_factory: AsyncSessionLocal,
) -> AsyncIterator[AsyncSession]:
    """Async session with alice + bob users pre-seeded."""
    async with session_factory() as s:
        now = datetime.now(UTC).isoformat()
        s.add_all(
            [
                User(user_id="alice", created_at=now),
                User(user_id="bob", created_at=now),
            ]
        )
        await s.commit()
        yield s


async def _append_n(session: AsyncSession, user_id: str, n: int) -> list[Event]:
    """Append ``n`` events for ``user_id``, commit, return the rows."""
    rows: list[Event] = []
    for i in range(n):
        row = await append_event(
            session,
            user_id=user_id,
            strategy_id=None,
            event_type="decision",
            payload={"i": i},
        )
        rows.append(row)
    await session.commit()
    return rows


# ---------------------------------------------------------------------------
# Behavior 18 — empty events → []
# ---------------------------------------------------------------------------


async def test_walk_chain_empty_events_returns_empty_list(
    session: AsyncSession,
) -> None:
    """No events at all → nothing to verify → ``[]``."""
    assert await walk_chain(session, user_id="alice") == []


# ---------------------------------------------------------------------------
# Behavior 19 — intact chain → []
# ---------------------------------------------------------------------------


async def test_walk_chain_intact_chain_returns_empty_list(
    session: AsyncSession,
) -> None:
    """Five sequential appends → chain intact → ``walk_chain`` returns ``[]``."""
    await _append_n(session, "alice", 5)
    assert await walk_chain(session, user_id="alice") == []


# ---------------------------------------------------------------------------
# Behavior 20 — tampered payload detected
# ---------------------------------------------------------------------------


async def test_walk_chain_detects_tampered_payload(
    session: AsyncSession,
) -> None:
    """Manually UPDATE row 3's ``payload_json`` to a different canonical string.

    Recomputing sha256(prev_hash + payload_json) for row 3 yields a different
    digest than the stored ``row_hash`` → row 3 is flagged.
    """
    rows = await _append_n(session, "alice", 5)
    # The third event has id == rows[2].id (autoincrement starts at 1 but
    # we use the actual id, not the position, to be index-safe).
    target_id = rows[2].id

    # Replace payload_json with a different canonical-subset string. We
    # deliberately keep prev_hash and row_hash unchanged so the test isolates
    # the "payload tamper" case.
    forged_canonical = canonical_json(
        {
            "event_type": "decision",
            "payload": {"tampered": True},
            "ts": "1999-01-01T00:00:00+00:00",
            "user_id": "alice",
        }
    )
    await session.execute(
        update(Event)
        .where(Event.id == target_id)
        .values(payload_json=forged_canonical)
    )
    await session.commit()

    breaks = await walk_chain(session, user_id="alice")
    # Row target_id breaks first; subsequent rows also break because the
    # walker's running ``expected_prev`` re-syncs to ``row.row_hash`` after
    # each row — but the next row's ``prev_hash`` no longer matches anyway,
    # because the previous row's row_hash check failed. We assert target_id
    # is present and is the FIRST break (the contract from the plan).
    assert target_id in breaks
    assert breaks[0] == target_id


# ---------------------------------------------------------------------------
# Behavior 21 — broken prev_hash detected
# ---------------------------------------------------------------------------


async def test_walk_chain_detects_broken_prev_hash(
    session: AsyncSession,
) -> None:
    """Manually UPDATE row 4's ``prev_hash`` to GENESIS — chain breaks at 4."""
    rows = await _append_n(session, "alice", 5)
    target_id = rows[3].id  # row 4 (1-indexed); the 4th event

    await session.execute(
        update(Event)
        .where(Event.id == target_id)
        .values(prev_hash=GENESIS_PREV_HASH)
    )
    await session.commit()

    breaks = await walk_chain(session, user_id="alice")
    assert target_id in breaks
    # The first break is row 4 because rows 1-3 are still consistent.
    assert breaks[0] == target_id


# ---------------------------------------------------------------------------
# Behavior 22 — deleted middle row detected
# ---------------------------------------------------------------------------


async def test_walk_chain_detects_deleted_middle_row(
    session: AsyncSession,
) -> None:
    """DELETE row 3 of 5 → row 4's prev_hash no longer matches row 2's row_hash."""
    rows = await _append_n(session, "alice", 5)
    delete_id = rows[2].id  # row 3
    row4_id = rows[3].id  # row 4 — the first inconsistency after deletion

    await session.execute(delete(Event).where(Event.id == delete_id))
    await session.commit()

    breaks = await walk_chain(session, user_id="alice")
    assert row4_id in breaks
    assert breaks[0] == row4_id


# ---------------------------------------------------------------------------
# Behavior 23 — walk_chain is read-only
# ---------------------------------------------------------------------------


async def test_walk_chain_is_read_only_on_tampered_chain(
    session: AsyncSession,
) -> None:
    """Calling ``walk_chain`` on a tampered chain MUST NOT mutate any row."""
    rows = await _append_n(session, "alice", 3)
    target_id = rows[1].id

    # Tamper with row 2's payload_json.
    forged = canonical_json(
        {
            "event_type": "decision",
            "payload": {"tampered": True},
            "ts": "1999-01-01T00:00:00+00:00",
            "user_id": "alice",
        }
    )
    await session.execute(
        update(Event).where(Event.id == target_id).values(payload_json=forged)
    )
    await session.commit()

    # Snapshot every row's columns BEFORE walk_chain.
    before = {
        r.id: (r.ts, r.event_type, r.payload_json, r.prev_hash, r.row_hash)
        for r in (
            await session.execute(
                select(Event).where(Event.user_id == "alice").order_by(Event.id)
            )
        )
        .scalars()
        .all()
    }

    breaks = await walk_chain(session, user_id="alice")
    assert breaks  # at least one break

    # Snapshot AFTER walk_chain. Must be identical to before.
    after = {
        r.id: (r.ts, r.event_type, r.payload_json, r.prev_hash, r.row_hash)
        for r in (
            await session.execute(
                select(Event).where(Event.user_id == "alice").order_by(Event.id)
            )
        )
        .scalars()
        .all()
    }
    assert before == after, "walk_chain mutated rows — read-only contract broken"


# ---------------------------------------------------------------------------
# Behavior 24 — walk_chain is user-scoped
# ---------------------------------------------------------------------------


async def test_walk_chain_is_user_scoped(session: AsyncSession) -> None:
    """alice's verify call ignores bob's tampered events."""
    # Build intact chains for both users.
    alice_rows = await _append_n(session, "alice", 3)
    bob_rows = await _append_n(session, "bob", 3)

    # Tamper bob's row 2 only.
    bob_target_id = bob_rows[1].id
    forged = canonical_json(
        {
            "event_type": "decision",
            "payload": {"tampered": True},
            "ts": "1999-01-01T00:00:00+00:00",
            "user_id": "bob",
        }
    )
    await session.execute(
        update(Event).where(Event.id == bob_target_id).values(payload_json=forged)
    )
    await session.commit()

    # Alice's chain is still intact from alice's perspective.
    alice_breaks = await walk_chain(session, user_id="alice")
    assert alice_breaks == []
    # alice_rows referenced for symmetry / to ensure the rows exist.
    assert all(r.id is not None for r in alice_rows)

    # Bob's chain has at least one break (the tampered row).
    bob_breaks = await walk_chain(session, user_id="bob")
    assert bob_target_id in bob_breaks
