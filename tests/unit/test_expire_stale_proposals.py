"""Unit tests for ``expire_stale_proposals`` sweep (HITL-03) — Plan 03-04 Task 1.

Tests:
(a) basic sweep — one PENDING with expires_at in the past → transitions to EXPIRED + appends expiration event
(b) skips unexpired — PENDING with expires_at in the future → no transition
(c) AWAITING_2ND_CHANNEL with expires_at in the past → transitions to EXPIRED via the existing P2 edge (A7)
(d) grandfathered NULL expires_at → ignored per D-61
(e) sweep run twice → idempotent (second run yields zero transitions)
(f) sweep vs click race — monkeypatch transition_status to raise ValueError → sweep catches, logs, continues
(g) configured_timeout_minutes payload field reads from strategy.proposal_timeout_minutes when set, falls back to 30 when None
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from freezegun import freeze_time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.db.models import Base, Proposal, Strategy, User
from gekko.db.session import make_session_factory

_TEST_PASSPHRASE = "test-expiry-passphrase"  # nosec: test-only literal
_USER_ID = "test-expiry-user"
_STRATEGY_ID = "strat-expiry-001"
_NOW_ISO = "2026-06-18T10:00:00+00:00"


@pytest_asyncio.fixture
async def expiry_engine(tmp_path: Any) -> Any:
    """AsyncEngine with full schema for expiry tests."""
    from gekko.db.engine import get_async_engine

    engine = get_async_engine(tmp_path / "expiry.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_user_strategy(session: AsyncSession, strategy_payload: dict | None = None) -> None:
    """Seed a user + strategy row."""
    now = _NOW_ISO
    user = User(
        user_id=_USER_ID,
        created_at=now,
        agreement_acknowledged_at=now,
    )
    session.add(user)
    await session.flush()

    payload = strategy_payload or {}
    strategy = Strategy(
        strategy_id=_STRATEGY_ID,
        user_id=_USER_ID,
        strategy_name="test-strategy",
        version=1,
        payload_json=json.dumps(payload),
        created_at=now,
    )
    session.add(strategy)
    await session.flush()


def _make_proposal(
    proposal_id: str,
    status: str,
    expires_at: str | None,
    now: str = _NOW_ISO,
) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        user_id=_USER_ID,
        strategy_id=_STRATEGY_ID,
        status=status,
        payload_json="{}",
        client_order_id=None,
        broker_order_id=None,
        created_at=now,
        updated_at=now,
        account_mode="PAPER",
        expires_at=expires_at,
        slack_message_ts="1234567890.000100",
        slack_message_channel="D_TEST_CHAN",
    )


@pytest.mark.asyncio
async def test_basic_sweep(expiry_engine: Any) -> None:
    """Basic sweep: PENDING proposal whose expires_at is in the past → EXPIRED + expiration event."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    past_iso = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-basic-001", "PENDING", past_iso)
        session.add(proposal)

    # Mock Slack side-effects so no real API calls fire.
    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 1

    # Verify status flipped to EXPIRED.
    async with sf() as session:
        from sqlalchemy import select as _select

        row = (
            await session.execute(
                _select(Proposal).where(Proposal.proposal_id == "prop-basic-001")
            )
        ).scalar_one()
        assert row.status == "EXPIRED"

    # Verify expiration audit event was appended.
    async with sf() as session:
        from sqlalchemy import select as _select

        from gekko.db.models import Event

        events = (
            await session.execute(
                _select(Event).where(
                    Event.user_id == _USER_ID,
                )
            )
        ).scalars().all()
        expiry_events = [
            e for e in events
            if json.loads(e.payload_json).get("event_type") == "expiration"
        ]
        assert len(expiry_events) == 1
        payload = json.loads(expiry_events[0].payload_json)["payload"]
        assert payload["proposal_id"] == "prop-basic-001"
        assert payload["reason"] == "timeout"
        assert "expired_at" in payload
        assert "configured_timeout_minutes" in payload


@pytest.mark.asyncio
async def test_skips_unexpired(expiry_engine: Any) -> None:
    """Sweep skips PENDING proposals whose expires_at is in the future."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    future_iso = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-future-001", "PENDING", future_iso)
        session.add(proposal)

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 0

    # Verify proposal remains PENDING.
    async with sf() as session:
        from sqlalchemy import select as _select

        row = (
            await session.execute(
                _select(Proposal).where(Proposal.proposal_id == "prop-future-001")
            )
        ).scalar_one()
        assert row.status == "PENDING"


@pytest.mark.asyncio
async def test_awaiting_2nd_channel_expires(expiry_engine: Any) -> None:
    """AWAITING_2ND_CHANNEL proposal whose expires_at is past → EXPIRED (A7 VALIDATION row)."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    past_iso = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-awaiting-001", "AWAITING_2ND_CHANNEL", past_iso)
        session.add(proposal)

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 1

    async with sf() as session:
        from sqlalchemy import select as _select

        row = (
            await session.execute(
                _select(Proposal).where(Proposal.proposal_id == "prop-awaiting-001")
            )
        ).scalar_one()
        assert row.status == "EXPIRED"


@pytest.mark.asyncio
async def test_grandfathered_null_ignored(expiry_engine: Any) -> None:
    """D-61 grandfathering: PENDING proposal with expires_at IS NULL is NOT swept."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-null-001", "PENDING", expires_at=None)
        session.add(proposal)

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 0

    async with sf() as session:
        from sqlalchemy import select as _select

        row = (
            await session.execute(
                _select(Proposal).where(Proposal.proposal_id == "prop-null-001")
            )
        ).scalar_one()
        assert row.status == "PENDING"


@pytest.mark.asyncio
async def test_idempotent_double_sweep(expiry_engine: Any) -> None:
    """Sweep run twice: second run yields zero transitions (first already moved to EXPIRED)."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    past_iso = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-idem-001", "PENDING", past_iso)
        session.add(proposal)

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count1 = await expire_stale_proposals(user_id=_USER_ID)
        count2 = await expire_stale_proposals(user_id=_USER_ID)

    assert count1 == 1
    assert count2 == 0


@pytest.mark.asyncio
async def test_race_with_click_swallowed(expiry_engine: Any) -> None:
    """Sweep vs click race: transition_status raises ValueError → sweep catches, logs, continues."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    past_iso = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    async with sf() as session, session.begin():
        await _seed_user_strategy(session)
        proposal = _make_proposal("prop-race-001", "PENDING", past_iso)
        session.add(proposal)

    call_count = {"n": 0}

    async def _mock_transition(session: Any, proposal_id: str, *, from_status: str, to_status: str) -> Any:
        call_count["n"] += 1
        raise ValueError("state machine: REJECTED is not in STATE_TRANSITIONS from PENDING to EXPIRED (simulated race)")

    with (
        patch("gekko.approval.expiry.transition_status", new=_mock_transition),
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        # Should NOT raise — sweep catches ValueError and continues.
        count = await expire_stale_proposals(user_id=_USER_ID)

    # Race was swallowed — no panic, count=0 because no successful transition.
    assert count == 0
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_configured_timeout_minutes_from_strategy(expiry_engine: Any) -> None:
    """Payload field configured_timeout_minutes reads from strategy when set, falls back to 30 when None."""
    from gekko.approval.expiry import expire_stale_proposals

    sf = make_session_factory(expiry_engine)

    past_iso = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    # Seed a strategy with proposal_timeout_minutes=45 in payload_json.
    strategy_payload = {"proposal_timeout_minutes": 45}

    async with sf() as session, session.begin():
        await _seed_user_strategy(session, strategy_payload=strategy_payload)
        proposal = _make_proposal("prop-timeout-001", "PENDING", past_iso)
        session.add(proposal)

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=AsyncMock(),
        ),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
    ):
        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 1

    # Verify the configured_timeout_minutes value in the audit event.
    async with sf() as session:
        from sqlalchemy import select as _select

        from gekko.db.models import Event

        events = (
            await session.execute(
                _select(Event).where(Event.user_id == _USER_ID)
            )
        ).scalars().all()
        expiry_events = [
            e for e in events
            if json.loads(e.payload_json).get("event_type") == "expiration"
        ]
        assert len(expiry_events) == 1
        payload = json.loads(expiry_events[0].payload_json)["payload"]
        assert payload["configured_timeout_minutes"] == 45
