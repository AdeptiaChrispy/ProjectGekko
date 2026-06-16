"""BLOCKER #1 STATE_TRANSITIONS frozenset Phase-2 edges + OrderGuardRejected — Plan 02-01 Task 5.

Plan 02-06 Task 2 originally claimed plan 02-01 added these edges; this plan
now actually does (Wave-1 foundational, NOT deferred). The 5 new edges:

  - (PENDING, AWAITING_2ND_CHANNEL)       — first-live-trade dual-channel diversion
  - (AWAITING_2ND_CHANNEL, APPROVED_LIVE) — dashboard /live-confirm fires
  - (AWAITING_2ND_CHANNEL, REJECTED)      — operator rejects on second channel
  - (AWAITING_2ND_CHANNEL, EXPIRED)       — reserved for future timeout path
  - (APPROVED_LIVE, EXECUTING)            — live-broker hand-off

Body-unchanged invariant (PATTERNS §3e): transition_status function body is
data-driven on STATE_TRANSITIONS; this plan extends the data, NOT the function.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Behaviors — STATE_TRANSITIONS frozenset (BLOCKER #1)
# ---------------------------------------------------------------------------


def test_state_transitions_contains_pending_to_awaiting_2nd_channel() -> None:
    """(PENDING, AWAITING_2ND_CHANNEL) is in STATE_TRANSITIONS."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert ("PENDING", "AWAITING_2ND_CHANNEL") in STATE_TRANSITIONS


def test_state_transitions_contains_awaiting_to_approved_live() -> None:
    """(AWAITING_2ND_CHANNEL, APPROVED_LIVE) is in STATE_TRANSITIONS."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert ("AWAITING_2ND_CHANNEL", "APPROVED_LIVE") in STATE_TRANSITIONS


def test_state_transitions_contains_awaiting_to_rejected() -> None:
    """(AWAITING_2ND_CHANNEL, REJECTED) is in STATE_TRANSITIONS."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert ("AWAITING_2ND_CHANNEL", "REJECTED") in STATE_TRANSITIONS


def test_state_transitions_contains_awaiting_to_expired() -> None:
    """(AWAITING_2ND_CHANNEL, EXPIRED) is in STATE_TRANSITIONS (reserved for future timeout)."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert ("AWAITING_2ND_CHANNEL", "EXPIRED") in STATE_TRANSITIONS


def test_state_transitions_contains_approved_live_to_executing() -> None:
    """(APPROVED_LIVE, EXECUTING) is in STATE_TRANSITIONS."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert ("APPROVED_LIVE", "EXECUTING") in STATE_TRANSITIONS


def test_state_transitions_retains_phase1_edges() -> None:
    """All 6 Phase-1 edges remain — no regression."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    phase1 = {
        ("PENDING", "APPROVED"),
        ("PENDING", "REJECTED"),
        ("APPROVED", "EXECUTING"),
        ("APPROVED", "FAILED"),
        ("EXECUTING", "FILLED"),
        ("EXECUTING", "FAILED"),
    }
    assert phase1 <= STATE_TRANSITIONS


def test_state_transitions_total_count_is_eleven() -> None:
    """6 Phase-1 + 5 Phase-2 = 11 total edges."""
    from gekko.approval.proposals import STATE_TRANSITIONS

    assert len(STATE_TRANSITIONS) == 11, (
        f"Expected 11 edges (6 P1 + 5 P2), got {len(STATE_TRANSITIONS)}: "
        f"{sorted(STATE_TRANSITIONS)}"
    )


def test_transition_status_body_unchanged() -> None:
    """PATTERNS §3e: transition_status is data-driven on the frozenset.

    The function body must NOT branch on the new states — adding the edges
    to the frozenset is sufficient. Verified by reading the function source.
    """
    from gekko.approval.proposals import transition_status

    src = inspect.getsource(transition_status)
    # The body MUST NOT contain a literal hardcoded check for Phase-2 states.
    assert '== "AWAITING_2ND_CHANNEL"' not in src, (
        "transition_status added a branch for AWAITING_2ND_CHANNEL — "
        "violates data-driven invariant (PATTERNS §3e)."
    )
    assert '== "APPROVED_LIVE"' not in src, (
        "transition_status added a branch for APPROVED_LIVE — "
        "violates data-driven invariant (PATTERNS §3e)."
    )


# ---------------------------------------------------------------------------
# Behaviors — OrderGuardRejected exception
# ---------------------------------------------------------------------------


def test_orderguard_rejected_carries_reject_code_reason_extra() -> None:
    """OrderGuardRejected exposes reject_code + reject_reason + extra attributes."""
    from gekko.core.errors import OrderGuardRejected

    err = OrderGuardRejected(
        "universe",
        "TSLA not in watchlist [NVDA, AMD]",
        extra={"ticker": "TSLA", "watchlist": ["NVDA", "AMD"]},
    )
    assert err.reject_code == "universe"
    assert err.reject_reason == "TSLA not in watchlist [NVDA, AMD]"
    assert err.extra == {"ticker": "TSLA", "watchlist": ["NVDA", "AMD"]}


def test_orderguard_rejected_default_extra_is_empty_dict() -> None:
    """OrderGuardRejected omitting extra keyword defaults to {}."""
    from gekko.core.errors import OrderGuardRejected

    err = OrderGuardRejected("hard_cap_position_pct", "9% > 5% cap")
    assert err.extra == {}


def test_orderguard_rejected_subclasses_gekko_error() -> None:
    """OrderGuardRejected is catchable as GekkoError (the family root)."""
    from gekko.core.errors import GekkoError, OrderGuardRejected

    err = OrderGuardRejected("kill_active", "kill switch is active")
    assert isinstance(err, GekkoError)


# ---------------------------------------------------------------------------
# Reachability against a real DB (transition path through Phase-2 states)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_transition_pending_to_awaiting_to_approved_live(
    temp_sqlcipher_db: Any,
) -> None:
    """Real-DB walk: PENDING -> AWAITING_2ND_CHANNEL -> APPROVED_LIVE chain.

    Confirms (a) the frozenset accepts the transitions, (b) the DB-layer
    CHECK constraint admits the new statuses (via Base.metadata.create_all
    which mirrors the Alembic 0002 vocabulary through _PROPOSAL_STATUSES).
    """
    from gekko.approval.proposals import transition_status
    from gekko.db.models import (
        Proposal as ProposalRow,
        Strategy as StrategyRow,
        User,
    )
    from gekko.db.session import make_session_factory

    Session = make_session_factory(temp_sqlcipher_db)
    proposal_id = uuid4().hex

    # Seed.
    async with Session() as session, session.begin():
        session.add(
            User(user_id="u1", created_at=datetime.now(UTC).isoformat())
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id="s1",
                user_id="u1",
                strategy_name="test",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id="u1",
                strategy_id="s1",
                status="PENDING",
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                account_mode="LIVE",
            )
        )

    # PENDING -> AWAITING_2ND_CHANNEL.
    async with Session() as session, session.begin():
        row = await transition_status(
            session,
            proposal_id,
            from_status="PENDING",
            to_status="AWAITING_2ND_CHANNEL",
        )
        assert row.status == "AWAITING_2ND_CHANNEL"

    # AWAITING_2ND_CHANNEL -> APPROVED_LIVE.
    async with Session() as session, session.begin():
        row = await transition_status(
            session,
            proposal_id,
            from_status="AWAITING_2ND_CHANNEL",
            to_status="APPROVED_LIVE",
        )
        assert row.status == "APPROVED_LIVE"

    # APPROVED_LIVE -> EXECUTING.
    async with Session() as session, session.begin():
        row = await transition_status(
            session,
            proposal_id,
            from_status="APPROVED_LIVE",
            to_status="EXECUTING",
        )
        assert row.status == "EXECUTING"


@pytest.mark.asyncio
async def test_phase2_invalid_transition_approved_to_approved_live_raises(
    temp_sqlcipher_db: Any,
) -> None:
    """(APPROVED, APPROVED_LIVE) is NOT in STATE_TRANSITIONS; transition_status raises ValueError."""
    from gekko.approval.proposals import transition_status
    from gekko.db.models import (
        Proposal as ProposalRow,
        Strategy as StrategyRow,
        User,
    )
    from gekko.db.session import make_session_factory

    Session = make_session_factory(temp_sqlcipher_db)
    proposal_id = uuid4().hex

    async with Session() as session, session.begin():
        session.add(
            User(user_id="u1", created_at=datetime.now(UTC).isoformat())
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id="s1",
                user_id="u1",
                strategy_name="test",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id="u1",
                strategy_id="s1",
                status="APPROVED",
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                account_mode="PAPER",
            )
        )

    async with Session() as session, session.begin():
        with pytest.raises(ValueError, match="Invalid proposal status transition"):
            await transition_status(
                session,
                proposal_id,
                from_status="APPROVED",
                to_status="APPROVED_LIVE",
            )
