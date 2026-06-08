"""Plan 01-04 Task 2 — append_event hash chain behaviors 9-17.

Each test maps 1:1 to a numbered behavior in PLAN.md `<feature>`:

 9. First event uses GENESIS_PREV_HASH.
10. Second event's prev_hash equals first event's row_hash.
11. row_hash is deterministic given inputs.
12. row_hash = sha256(prev_hash_bytes + canonical_subset_bytes).
13. payload_json stores the FULL canonical-subset string (Pattern 3 lock-in).
14. Concurrent appends from N asyncio tasks don't collide on prev_hash.
15. user_id scoping — alice / bob have independent chains.
16. Returns the inserted Event row.
17. append_event NEVER raises AuditChainBroken — only writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gekko.audit.canonical import GENESIS_PREV_HASH, canonical_json
from gekko.audit.log import append_event
from gekko.db.engine import get_async_engine
from gekko.db.models import Base, Event, User
from gekko.db.session import AsyncSessionLocal, make_session_factory

PASSPHRASE = "test-audit-chain-passphrase"  # nosec: test-only literal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Encrypted DB with the 6 P1 tables."""
    eng = get_async_engine(tmp_path / "audit-chain.db", PASSPHRASE)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> AsyncSessionLocal:
    """Async session factory bound to the test engine."""
    return make_session_factory(engine)


@pytest_asyncio.fixture
async def session(
    session_factory: AsyncSessionLocal,
) -> AsyncIterator[AsyncSession]:
    """A single async session for sequential tests.

    Concurrency tests open their own per-task sessions and skip this fixture.
    """
    async with session_factory() as s:
        # Seed users so FK constraints don't fire.
        s.add(User(user_id="alice", created_at=datetime.now(UTC).isoformat()))
        s.add(User(user_id="bob", created_at=datetime.now(UTC).isoformat()))
        await s.commit()
        yield s


async def _seed_users(factory: AsyncSessionLocal) -> None:
    """Used by concurrency test which doesn't share the `session` fixture."""
    async with factory() as s:
        s.add(User(user_id="alice", created_at=datetime.now(UTC).isoformat()))
        s.add(User(user_id="bob", created_at=datetime.now(UTC).isoformat()))
        await s.commit()


# ---------------------------------------------------------------------------
# Behavior 9 — First event uses GENESIS_PREV_HASH
# ---------------------------------------------------------------------------


async def test_first_event_uses_genesis_prev_hash(
    session: AsyncSession,
) -> None:
    """Empty events table → ``prev_hash == "0" * 64``."""
    row = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
    )
    await session.commit()
    assert row.prev_hash == GENESIS_PREV_HASH


# ---------------------------------------------------------------------------
# Behavior 10 — second event's prev_hash equals first event's row_hash
# ---------------------------------------------------------------------------


async def test_second_event_prev_hash_is_first_row_hash(
    session: AsyncSession,
) -> None:
    """Chain links — event[1].prev_hash == event[0].row_hash."""
    e0 = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"a": 1},
    )
    e1 = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"a": 2},
    )
    await session.commit()
    assert e1.prev_hash == e0.row_hash


# ---------------------------------------------------------------------------
# Behavior 11 — row_hash is deterministic given inputs
# ---------------------------------------------------------------------------


async def test_row_hash_deterministic_given_same_canonical_subset(
    session: AsyncSession,
) -> None:
    """Same prev_hash + same canonical-subset bytes → same row_hash.

    We construct the canonical subset by hand and hash it; the row written
    by append_event MUST match that computation. (This also doubles as the
    Behavior 12 manual recomputation check below; the determinism contract
    is what we're hitting here.)
    """
    # Manually compute what row_hash WILL be for a known canonical subset.
    ts = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC).isoformat()
    canonical = canonical_json(
        {
            "event_type": "decision",
            "payload": {"x": 1},
            "ts": ts,
            "user_id": "alice",
        }
    )
    expected_row_hash = hashlib.sha256(
        GENESIS_PREV_HASH.encode("ascii") + canonical.encode("utf-8")
    ).hexdigest()

    # Now write it via append_event with the same ts (pinned).
    row = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
        ts=ts,
    )
    await session.commit()
    assert row.row_hash == expected_row_hash


# ---------------------------------------------------------------------------
# Behavior 12 — row_hash = sha256(prev_hash_bytes + canonical_subset_bytes)
# ---------------------------------------------------------------------------


async def test_row_hash_input_is_prev_hash_bytes_plus_canonical_subset(
    session: AsyncSession,
) -> None:
    """Recompute hash from the stored row's columns; must equal row_hash."""
    row = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"ticker": "NVDA", "qty": 5},
    )
    await session.commit()

    # payload_json IS the canonical-subset string (Behavior 13). So
    # recomputing is just sha256(prev_hash || payload_json).
    recomputed = hashlib.sha256(
        row.prev_hash.encode("ascii") + row.payload_json.encode("utf-8")
    ).hexdigest()
    assert recomputed == row.row_hash


# ---------------------------------------------------------------------------
# Behavior 13 — payload_json stores the canonical-subset string
# ---------------------------------------------------------------------------


async def test_payload_json_is_canonical_subset_string_not_inner_payload(
    session: AsyncSession,
) -> None:
    """Pattern 3 lock-in: ``payload_json`` is the FULL canonical subset.

    Verify-time hashing is then a one-liner — see Behavior 12 above.
    """
    row = await append_event(
        session,
        user_id="alice",
        strategy_id="strat-abc",
        event_type="decision",
        payload={"ticker": "NVDA", "qty": 5},
    )
    await session.commit()

    parsed = json.loads(row.payload_json)
    # All four canonical-subset keys present.
    assert set(parsed.keys()) == {"event_type", "payload", "ts", "user_id"}
    assert parsed["event_type"] == "decision"
    assert parsed["user_id"] == "alice"
    assert parsed["payload"] == {"ticker": "NVDA", "qty": 5}
    # ts is a string (ISO format).
    assert isinstance(parsed["ts"], str)


async def test_payload_json_does_not_contain_strategy_id_outside_payload(
    session: AsyncSession,
) -> None:
    """``strategy_id`` is a separate column — not part of the canonical subset.

    Future ``strategy_id`` mutations (e.g., backfill / rename) must not
    invalidate the chain; the chain is over ``{event_type, payload, ts,
    user_id}`` only.
    """
    row = await append_event(
        session,
        user_id="alice",
        strategy_id="strat-abc",
        event_type="decision",
        payload={"x": 1},
    )
    await session.commit()
    parsed = json.loads(row.payload_json)
    assert "strategy_id" not in parsed


# ---------------------------------------------------------------------------
# Behavior 14 — concurrent appends serialize via asyncio.Lock
# ---------------------------------------------------------------------------


async def test_concurrent_appends_form_an_intact_chain(
    session_factory: AsyncSessionLocal,
) -> None:
    """50 concurrent ``append_event`` calls produce a linked chain.

    Each call opens its OWN session (independent SQLAlchemy transactions);
    the per-user ``asyncio.Lock`` inside ``append_event`` is what prevents
    two callers from reading the same prev_hash and writing the same row.
    """
    await _seed_users(session_factory)

    async def _append_one(i: int) -> None:
        async with session_factory() as s:
            await append_event(
                s,
                user_id="alice",
                strategy_id=None,
                event_type="decision",
                payload={"i": i},
            )
            await s.commit()

    await asyncio.gather(*(_append_one(i) for i in range(50)))

    # Read back ordered by id; chain must be intact.
    async with session_factory() as s:
        q = (
            select(Event)
            .where(Event.user_id == "alice")
            .order_by(Event.id.asc())
        )
        rows = list((await s.execute(q)).scalars().all())

    assert len(rows) == 50
    assert rows[0].prev_hash == GENESIS_PREV_HASH
    for i in range(len(rows) - 1):
        assert rows[i + 1].prev_hash == rows[i].row_hash, (
            f"chain broken at index {i}: prev_hash mismatch"
        )


# ---------------------------------------------------------------------------
# Behavior 15 — user_id scoping (alice/bob chains are independent)
# ---------------------------------------------------------------------------


async def test_user_id_scoping_alice_and_bob_chains_independent(
    session: AsyncSession,
) -> None:
    """Bob's first event uses GENESIS, not alice's row_hash."""
    alice_e0 = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
    )
    bob_e0 = await append_event(
        session,
        user_id="bob",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
    )
    await session.commit()

    # Each user's first event starts from genesis.
    assert alice_e0.prev_hash == GENESIS_PREV_HASH
    assert bob_e0.prev_hash == GENESIS_PREV_HASH
    # row_hashes differ because user_id is in the canonical subset.
    assert alice_e0.row_hash != bob_e0.row_hash

    # Bob's second event chains off bob_e0, NOT alice_e0.
    bob_e1 = await append_event(
        session,
        user_id="bob",
        strategy_id=None,
        event_type="decision",
        payload={"x": 2},
    )
    await session.commit()
    assert bob_e1.prev_hash == bob_e0.row_hash


# ---------------------------------------------------------------------------
# Behavior 16 — returns the inserted Event row
# ---------------------------------------------------------------------------


async def test_append_event_returns_the_inserted_row(
    session: AsyncSession,
) -> None:
    """Result is an ``Event`` instance with populated id / prev_hash / row_hash."""
    row = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
    )
    await session.commit()
    assert isinstance(row, Event)
    assert row.id is not None and row.id > 0
    assert row.prev_hash == GENESIS_PREV_HASH
    assert len(row.row_hash) == 64  # sha256 hex digest
    assert row.user_id == "alice"
    assert row.event_type == "decision"


# ---------------------------------------------------------------------------
# Behavior 17 — append_event never raises AuditChainBroken
# ---------------------------------------------------------------------------


async def test_append_event_never_raises_auditchainbroken(
    session: AsyncSession,
) -> None:
    """Write-side never validates the chain. Verification is walk_chain's job."""
    from gekko.core.errors import AuditChainBroken

    # Write a row, then tamper with it; subsequent append_event must NOT
    # raise — it should still write a new row (with prev_hash = the tampered
    # row's row_hash). Detection is walk_chain's job, not append_event's.
    e0 = await append_event(
        session,
        user_id="alice",
        strategy_id=None,
        event_type="decision",
        payload={"x": 1},
    )
    await session.commit()
    e0.payload_json = "TAMPERED"  # mutate the in-memory row
    await session.commit()

    try:
        e1 = await append_event(
            session,
            user_id="alice",
            strategy_id=None,
            event_type="decision",
            payload={"x": 2},
        )
        await session.commit()
    except AuditChainBroken as exc:  # pragma: no cover
        raise AssertionError(
            "append_event must not raise AuditChainBroken — verification is "
            "walk_chain's job"
        ) from exc
    assert e1.id is not None
