"""Tests for daily_pnl audit event honesty — Plan 03-09 Task 2 (CR-03).

CR-03: send_daily_pnl_digest writes a daily_pnl audit event claiming the digest was
delivered even when _send_dm_blocks_respecting_quiet_hours silently suppressed the DM
due to quiet hours. The audit log must reflect reality.

Behaviors tested:
1. send_daily_pnl_digest when DM is suppressed by quiet hours -> the daily_pnl audit
   event written to DB has {"delivered": false, "suppressed_by_quiet_hours": true}
2. send_daily_pnl_digest when DM is sent -> the daily_pnl audit event has
   {"delivered": true, "suppressed_by_quiet_hours": false}
3. _send_dm_blocks_respecting_quiet_hours returns True when DM is dispatched,
   False when suppressed.

Monkeypatch target: gekko.approval.quiet_hours._resolve_quiet_hours
(patch-where-defined — the import is deferred inside _send_dm_blocks_respecting_quiet_hours
so patching gekko.reporter.daily_pnl._resolve_quiet_hours does NOT work).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from gekko.db.models import Base, Event, Strategy, User
from gekko.db.session import make_session_factory

_TEST_PASSPHRASE = "test-audit-honesty-passphrase"  # nosec: test-only
_USER_ID = "test-audit-honesty-user"
_STRATEGY_ID = "strat-audit-honesty-001"


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def audit_engine(tmp_path: Any) -> Any:
    """AsyncEngine with full schema for audit honesty tests."""
    from gekko.db.engine import get_async_engine

    engine = get_async_engine(tmp_path / "audit_honesty_test.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_strategy(session: Any) -> None:
    """Seed user + strategy rows."""
    now_iso = datetime.now(UTC).isoformat()
    user = User(
        user_id=_USER_ID,
        created_at=now_iso,
        agreement_acknowledged_at=now_iso,
    )
    session.add(user)
    await session.flush()

    session.add(
        Strategy(
            strategy_id=_STRATEGY_ID,
            user_id=_USER_ID,
            strategy_name="test-honesty-strategy",
            version=1,
            payload_json="{}",
            created_at=now_iso,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Test 1: DM suppressed by quiet hours -> audit event records delivered=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_pnl_audit_honest_when_suppressed(audit_engine: Any) -> None:
    """When DM is suppressed by quiet hours, audit event must record delivered=False."""
    from sqlalchemy import select

    sf = make_session_factory(audit_engine)

    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)

    mock_nyse = MagicMock()
    mock_schedule = MagicMock()
    mock_schedule.empty = False
    mock_nyse.schedule.return_value = mock_schedule

    mock_dm_blocks = AsyncMock()

    with (
        patch("gekko.reporter.daily_pnl.mcal") as mock_mcal,
        patch("gekko.reporter.daily_pnl._get_session_factory", return_value=(sf, None)),
        # Patch quiet_hours predicate where defined to return True (quiet window active).
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=True),
        ),
        # Patch _send_slack_dm_blocks so no real Slack call fires.
        patch(
            "gekko.execution.executor._send_slack_dm_blocks",
            new=AsyncMock(),
        ),
    ):
        mock_mcal.get_calendar.return_value = mock_nyse

        from gekko.reporter.daily_pnl import send_daily_pnl_digest

        result = await send_daily_pnl_digest(user_id=_USER_ID)

    # Function should still return True (it attempted delivery).
    assert result is True

    # Read the daily_pnl audit event.
    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(
                    Event.user_id == _USER_ID,
                    Event.event_type == "daily_pnl",
                )
            )
        ).scalars().all()

    assert len(events) == 1, f"Expected 1 daily_pnl audit event, got {len(events)}"

    outer = json.loads(events[0].payload_json)
    payload = outer.get("payload", outer)

    assert payload.get("delivered") is False, (
        f"Expected delivered=False when DM is suppressed by quiet hours, got {payload.get('delivered')!r}. "
        "CR-03: audit event must reflect actual DM delivery status."
    )
    assert payload.get("suppressed_by_quiet_hours") is True, (
        f"Expected suppressed_by_quiet_hours=True, got {payload.get('suppressed_by_quiet_hours')!r}. "
        "CR-03: audit event must record that suppression occurred."
    )


# ---------------------------------------------------------------------------
# Test 2: DM sent successfully -> audit event records delivered=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_pnl_audit_honest_when_sent(audit_engine: Any) -> None:
    """When DM is sent, audit event must record delivered=True."""
    from sqlalchemy import select

    sf = make_session_factory(audit_engine)

    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)

    mock_nyse = MagicMock()
    mock_schedule = MagicMock()
    mock_schedule.empty = False
    mock_nyse.schedule.return_value = mock_schedule

    with (
        patch("gekko.reporter.daily_pnl.mcal") as mock_mcal,
        patch("gekko.reporter.daily_pnl._get_session_factory", return_value=(sf, None)),
        # Patch quiet_hours predicate to return False (quiet window NOT active).
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=False),
        ),
        # Patch _send_slack_dm_blocks so no real Slack call fires.
        patch(
            "gekko.execution.executor._send_slack_dm_blocks",
            new=AsyncMock(),
        ),
    ):
        mock_mcal.get_calendar.return_value = mock_nyse

        from gekko.reporter.daily_pnl import send_daily_pnl_digest

        result = await send_daily_pnl_digest(user_id=_USER_ID)

    assert result is True

    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(
                    Event.user_id == _USER_ID,
                    Event.event_type == "daily_pnl",
                )
            )
        ).scalars().all()

    assert len(events) == 1

    outer = json.loads(events[0].payload_json)
    payload = outer.get("payload", outer)

    assert payload.get("delivered") is True, (
        f"Expected delivered=True when DM is sent, got {payload.get('delivered')!r}. "
        "CR-03: audit event must record actual delivery status."
    )
    assert payload.get("suppressed_by_quiet_hours") is False, (
        f"Expected suppressed_by_quiet_hours=False when DM sent, got {payload.get('suppressed_by_quiet_hours')!r}."
    )


# ---------------------------------------------------------------------------
# Test 3: _send_dm_blocks_respecting_quiet_hours returns bool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_dm_blocks_returns_bool_dispatched() -> None:
    """_send_dm_blocks_respecting_quiet_hours must return True when DM dispatched."""
    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=False),
        ),
        patch("gekko.execution.executor._send_slack_dm_blocks", new=AsyncMock()),
    ):
        from gekko.reporter.daily_pnl import _send_dm_blocks_respecting_quiet_hours

        result = await _send_dm_blocks_respecting_quiet_hours(
            _USER_ID,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}],
            category="daily_pnl",
            fallback="test",
        )

    assert result is True, (
        f"Expected True when DM is dispatched, got {result!r}. "
        "CR-03: _send_dm_blocks_respecting_quiet_hours must return bool."
    )


@pytest.mark.asyncio
async def test_send_dm_blocks_returns_bool_suppressed() -> None:
    """_send_dm_blocks_respecting_quiet_hours must return False when DM suppressed."""
    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=True),
        ),
        patch("gekko.execution.executor._send_slack_dm_blocks", new=AsyncMock()),
    ):
        from gekko.reporter.daily_pnl import _send_dm_blocks_respecting_quiet_hours

        result = await _send_dm_blocks_respecting_quiet_hours(
            _USER_ID,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}],
            category="daily_pnl",
            fallback="test",
        )

    assert result is False, (
        f"Expected False when DM is suppressed, got {result!r}. "
        "CR-03: _send_dm_blocks_respecting_quiet_hours must return False on suppression."
    )
