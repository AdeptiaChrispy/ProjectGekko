"""Plan 01-03 Task 2 — SQLAlchemy ORM models for the 6 P1 tables.

Per D-05 (snapshot-row strategy versioning), D-14 (single events table with
event_type discriminator + payload_json), and D-21 (user_id on every table).
All 10 behavior tests from PLAN.md Task 2.

The 6 P1 tables created by ``Base.metadata.create_all`` are:
    users, strategies, guidance, proposals, events, broker_credentials.

APScheduler's ``apscheduler_jobs`` table is intentionally NOT in this list —
APScheduler 3.x's ``SQLAlchemyJobStore`` creates it at runtime in Plan 01-09.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gekko.db.engine import get_async_engine
from gekko.db.models import (
    Base,
    BrokerCredential,
    Event,
    Guidance,
    Proposal,
    Strategy,
    User,
)
from gekko.db.session import AsyncSessionLocal, make_session_factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


PASSPHRASE = "test-models-passphrase"  # nosec: test-only literal


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Encrypted DB with the 6 P1 tables created via ``Base.metadata.create_all``."""
    eng = get_async_engine(tmp_path / "models.db", PASSPHRASE)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Async session factory bound to the test engine."""
    factory: AsyncSessionLocal = make_session_factory(engine)
    async with factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Schema introspection — the 6 P1 tables exist with user_id everywhere
# ---------------------------------------------------------------------------


async def test_metadata_creates_six_p1_tables(engine: AsyncEngine) -> None:
    """``Base.metadata.create_all`` succeeds and creates exactly the 6 P1 tables."""

    def _tables(sync_conn: object) -> set[str]:
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(_tables)

    assert names == {
        "users",
        "strategies",
        "guidance",
        "proposals",
        "events",
        "broker_credentials",
    }


async def test_every_table_has_user_id_column(engine: AsyncEngine) -> None:
    """D-21 — every data row in P1 carries a ``user_id`` column."""

    def _columns(sync_conn: object, name: str) -> set[str]:
        return {c["name"] for c in inspect(sync_conn).get_columns(name)}

    async with engine.connect() as conn:
        for table_name in (
            "users",
            "strategies",
            "guidance",
            "proposals",
            "events",
            "broker_credentials",
        ):
            cols = await conn.run_sync(_columns, table_name)
            assert "user_id" in cols, (
                f"table {table_name!r} missing user_id column (D-21)"
            )


async def test_events_table_d14_columns(engine: AsyncEngine) -> None:
    """D-14 — events table has exactly the documented columns."""

    def _columns(sync_conn: object) -> set[str]:
        return {c["name"] for c in inspect(sync_conn).get_columns("events")}

    async with engine.connect() as conn:
        cols = await conn.run_sync(_columns)

    # D-14 schema: id, ts, user_id, strategy_id, event_type, payload_json,
    # prev_hash, row_hash
    assert cols == {
        "id",
        "ts",
        "user_id",
        "strategy_id",
        "event_type",
        "payload_json",
        "prev_hash",
        "row_hash",
    }


# ---------------------------------------------------------------------------
# Inserts — golden-path round-trip
# ---------------------------------------------------------------------------


async def test_insert_user(session: AsyncSession) -> None:
    """User can be inserted with user_id as primary key."""
    user = User(user_id="alice", created_at=_iso_now())
    session.add(user)
    await session.commit()

    fetched = (
        await session.execute(select(User).where(User.user_id == "alice"))
    ).scalar_one()
    assert fetched.user_id == "alice"


async def test_insert_strategy_unique_constraint(session: AsyncSession) -> None:
    """D-05 — composite unique on (user_id, strategy_name, version) rejects dupes."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    s1 = Strategy(
        strategy_id="s1",
        user_id="alice",
        strategy_name="ai-infra",
        version=1,
        payload_json='{"thesis": "v1"}',
        created_at=_iso_now(),
    )
    session.add(s1)
    await session.commit()

    # Same (user_id, strategy_name, version) — must fail
    s1_dup = Strategy(
        strategy_id="s1-dup",
        user_id="alice",
        strategy_name="ai-infra",
        version=1,
        payload_json='{"thesis": "v1-dup"}',
        created_at=_iso_now(),
    )
    session.add(s1_dup)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_insert_guidance_with_scope(session: AsyncSession) -> None:
    """Guidance row inserts with scope='strategy'."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    g = Guidance(
        guidance_id="g1",
        user_id="alice",
        strategy_id=None,
        text="focus on energy",
        scope="strategy",
        created_at=_iso_now(),
        expires_at=None,
    )
    session.add(g)
    await session.commit()

    fetched = (
        await session.execute(select(Guidance).where(Guidance.guidance_id == "g1"))
    ).scalar_one()
    assert fetched.scope == "strategy"
    assert fetched.text == "focus on energy"


async def test_guidance_scope_check_constraint(session: AsyncSession) -> None:
    """CheckConstraint rejects invalid scope values (must be 'strategy' or 'global')."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    bad = Guidance(
        guidance_id="g-bad",
        user_id="alice",
        text="bad scope",
        scope="invalid-scope",
        created_at=_iso_now(),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_insert_proposal(session: AsyncSession) -> None:
    """Proposal can be inserted with status='PENDING' and a payload_json."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()
    s = Strategy(
        strategy_id="s1",
        user_id="alice",
        strategy_name="ai-infra",
        version=1,
        payload_json="{}",
        created_at=_iso_now(),
    )
    session.add(s)
    await session.commit()

    now = _iso_now()
    p = Proposal(
        proposal_id="p1",
        user_id="alice",
        strategy_id="s1",
        status="PENDING",
        payload_json='{"ticker": "NVDA", "side": "buy"}',
        created_at=now,
        updated_at=now,
    )
    session.add(p)
    await session.commit()

    fetched = (
        await session.execute(select(Proposal).where(Proposal.proposal_id == "p1"))
    ).scalar_one()
    assert fetched.status == "PENDING"


async def test_proposal_status_check_constraint(session: AsyncSession) -> None:
    """CheckConstraint rejects invalid status values."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()
    s = Strategy(
        strategy_id="s1",
        user_id="alice",
        strategy_name="ai-infra",
        version=1,
        payload_json="{}",
        created_at=_iso_now(),
    )
    session.add(s)
    await session.commit()

    now = _iso_now()
    bad = Proposal(
        proposal_id="p-bad",
        user_id="alice",
        strategy_id="s1",
        status="NOT_A_REAL_STATUS",
        payload_json="{}",
        created_at=now,
        updated_at=now,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_insert_event_kill_switch_nullable_strategy_id(
    session: AsyncSession,
) -> None:
    """D-14 — kill_switch event has strategy_id=NULL (not strategy-scoped)."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    e = Event(
        ts=_iso_now(),
        user_id="alice",
        strategy_id=None,  # kill_switch is global
        event_type="kill_switch",
        payload_json='{"reason": "user-initiated"}',
        prev_hash="0" * 64,
        row_hash="a" * 64,
    )
    session.add(e)
    await session.commit()

    fetched = (
        await session.execute(select(Event).where(Event.user_id == "alice"))
    ).scalar_one()
    assert fetched.event_type == "kill_switch"
    assert fetched.strategy_id is None


async def test_event_event_type_check_constraint(session: AsyncSession) -> None:
    """CheckConstraint rejects unknown event_type values (D-14 vocabulary)."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    bad = Event(
        ts=_iso_now(),
        user_id="alice",
        event_type="not-a-real-event-type",
        payload_json="{}",
        prev_hash="0" * 64,
        row_hash="a" * 64,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_insert_broker_credential(session: AsyncSession) -> None:
    """BrokerCredential row inserts with composite PK (user_id, broker)."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    bc = BrokerCredential(
        user_id="alice",
        broker="alpaca",
        key_blob="PKTEST...",
        secret_blob="secret...",
        paper=True,
        created_at=_iso_now(),
    )
    session.add(bc)
    await session.commit()

    fetched = (
        await session.execute(
            select(BrokerCredential).where(BrokerCredential.user_id == "alice")
        )
    ).scalar_one()
    assert fetched.broker == "alpaca"
    assert fetched.paper is True


# ---------------------------------------------------------------------------
# D-05 snapshot versioning — latest-first ordering
# ---------------------------------------------------------------------------


async def test_strategy_latest_version_query(session: AsyncSession) -> None:
    """Querying Strategy by (user_id, strategy_name) ORDER BY version DESC returns latest first."""
    session.add(User(user_id="alice", created_at=_iso_now()))
    await session.commit()

    for v in (1, 2, 3):
        session.add(
            Strategy(
                strategy_id=f"s-{v}",
                user_id="alice",
                strategy_name="ai-infra",
                version=v,
                payload_json=f'{{"v": {v}}}',
                created_at=_iso_now(),
            )
        )
    await session.commit()

    stmt = (
        select(Strategy)
        .where(
            Strategy.user_id == "alice",
            Strategy.strategy_name == "ai-infra",
        )
        .order_by(Strategy.version.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    assert [r.version for r in rows] == [3, 2, 1]


# ---------------------------------------------------------------------------
# __repr__ defense-in-depth — payload_json and *_blob never in repr
# ---------------------------------------------------------------------------


def test_proposal_repr_excludes_payload_json() -> None:
    """Defense-in-depth — Proposal.__repr__ never echoes payload_json."""
    now = _iso_now()
    p = Proposal(
        proposal_id="p-secret",
        user_id="alice",
        strategy_id="s1",
        status="PENDING",
        payload_json='{"super": "secret-rationale"}',
        created_at=now,
        updated_at=now,
    )
    r = repr(p)
    assert "secret-rationale" not in r
    assert "payload_json" not in r


def test_broker_credential_repr_excludes_blobs() -> None:
    """Defense-in-depth — BrokerCredential.__repr__ never echoes key_blob / secret_blob."""
    bc = BrokerCredential(
        user_id="alice",
        broker="alpaca",
        key_blob="PK-SUPER-SECRET-KEY",
        secret_blob="ULTRA-SECRET-SECRET",
        paper=True,
        created_at=_iso_now(),
    )
    r = repr(bc)
    assert "PK-SUPER-SECRET-KEY" not in r
    assert "ULTRA-SECRET-SECRET" not in r
    assert "key_blob" not in r
    assert "secret_blob" not in r
