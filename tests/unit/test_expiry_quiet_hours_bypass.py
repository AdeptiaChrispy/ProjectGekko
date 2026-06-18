"""Tests for expiry DM quiet-hours bypass — Plan 03-09 Task 2 (CR-04).

CR-04: The expiry sweep DMs the operator with category=routine_fill, which is
suppressed during quiet hours. A real-money proposal expiring during the quiet
window produces zero operator signal.

Fix: Change expiry DM category to executor_error (non-suppressible bypass category
per D-48), ensuring the operator always receives expiry notifications.

Behaviors tested:
4. expire_stale_proposals when current time is inside the operator's quiet window
   -> _send_slack_dm_respecting_quiet_hours is still called with category="executor_error"
   (bypass category guarantees delivery regardless of quiet hours; assert called once)
5. expire_stale_proposals when current time is outside quiet window
   -> DM fires as before (still called with category="executor_error")

Monkeypatch target: gekko.approval.quiet_hours._resolve_quiet_hours
(patch-where-defined — deferred import inside _send_slack_dm_respecting_quiet_hours).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from gekko.db.models import Base, Proposal, Strategy, User
from gekko.db.session import make_session_factory

_TEST_PASSPHRASE = "test-expiry-bypass-passphrase"  # nosec: test-only
_USER_ID = "test-expiry-bypass-user"
_STRATEGY_ID = "strat-expiry-bypass-001"
_NOW_ISO = "2026-06-18T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def bypass_engine(tmp_path: Any) -> Any:
    """AsyncEngine with full schema for expiry bypass tests."""
    from gekko.db.engine import get_async_engine

    engine = get_async_engine(tmp_path / "expiry_bypass_test.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_strategy_expired_proposal(session: Any) -> None:
    """Seed user + strategy + one PENDING proposal with expires_at in the past."""
    now_iso = _NOW_ISO
    past_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

    session.add(User(user_id=_USER_ID, created_at=now_iso, agreement_acknowledged_at=now_iso))
    await session.flush()

    session.add(
        Strategy(
            strategy_id=_STRATEGY_ID,
            user_id=_USER_ID,
            strategy_name="test-bypass-strategy",
            version=1,
            payload_json="{}",
            created_at=now_iso,
        )
    )
    await session.flush()

    session.add(
        Proposal(
            proposal_id="prop-bypass-001",
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID,
            status="PENDING",
            payload_json="{}",
            client_order_id=None,
            broker_order_id=None,
            created_at=now_iso,
            updated_at=now_iso,
            account_mode="PAPER",
            expires_at=past_iso,
            slack_message_ts="1234567890.000100",
            slack_message_channel="D_TEST_CHAN",
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Test 4: Expiry DM fires during quiet hours via executor_error bypass category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_dm_fires_during_quiet_hours_via_bypass(bypass_engine: Any) -> None:
    """expire_stale_proposals must send DM with category=executor_error even during quiet hours.

    CR-04 fix: category changed from routine_fill to executor_error (bypass category).
    The executor_error bypass causes _send_slack_dm_respecting_quiet_hours to skip
    the quiet-hours check and fire the DM immediately.
    """
    sf = make_session_factory(bypass_engine)

    async with sf() as session, session.begin():
        await _seed_user_strategy_expired_proposal(session)

    mock_send_dm = AsyncMock()

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
        # Patch the DM function at point of use so we can assert the category.
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=mock_send_dm,
        ),
        # Quiet hours predicate says we ARE in the quiet window.
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=True),
        ),
    ):
        from gekko.approval.expiry import expire_stale_proposals

        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 1, f"Expected 1 expired proposal, got {count}"

    # The DM must have been called exactly once.
    assert mock_send_dm.call_count == 1, (
        f"Expected DM called once, got {mock_send_dm.call_count}. "
        "CR-04: expiry DM must fire even during quiet hours via executor_error bypass."
    )

    # The category argument must be executor_error (not routine_fill).
    call_kwargs = mock_send_dm.call_args
    # Positional call: _send_slack_dm_respecting_quiet_hours(user_id, text, category=...)
    category_arg = call_kwargs.kwargs.get("category") or (
        call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
    )
    assert category_arg == "executor_error", (
        f"Expected category='executor_error', got {category_arg!r}. "
        "CR-04: expiry DM must use executor_error (non-suppressible bypass category per D-48)."
    )


# ---------------------------------------------------------------------------
# Test 5: Expiry DM fires outside quiet hours (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_dm_fires_outside_quiet_hours(bypass_engine: Any) -> None:
    """expire_stale_proposals must send DM normally when quiet hours are NOT active."""
    sf = make_session_factory(bypass_engine)

    async with sf() as session, session.begin():
        await _seed_user_strategy_expired_proposal(session)

    mock_send_dm = AsyncMock()

    with (
        patch("gekko.approval.expiry._chat_update_expired_card", new=AsyncMock()),
        patch(
            "gekko.approval.expiry._get_session_factory",
            return_value=(sf, None),
        ),
        patch(
            "gekko.execution.executor._send_slack_dm_respecting_quiet_hours",
            new=mock_send_dm,
        ),
        # Quiet hours predicate says we are NOT in the quiet window.
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=False),
        ),
    ):
        from gekko.approval.expiry import expire_stale_proposals

        count = await expire_stale_proposals(user_id=_USER_ID)

    assert count == 1

    assert mock_send_dm.call_count == 1, (
        f"Expected DM called once outside quiet hours, got {mock_send_dm.call_count}"
    )

    call_kwargs = mock_send_dm.call_args
    category_arg = call_kwargs.kwargs.get("category") or (
        call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
    )
    assert category_arg == "executor_error", (
        f"Expected category='executor_error' outside quiet hours too, got {category_arg!r}. "
        "CR-04: expiry DM category must be executor_error in all cases."
    )
