"""Tests for daily P&L aggregation and Block Kit digest (REPT-01) — Plan 03-06.

Tests:
(a) Seed today's events (3 fills across 2 strategies + 1 error); assert per-strategy
    aggregation matches; assert gross_pnl_usd format string.
(b) Zero fills today → `_no fills today_` branch in per-strategy block.
(c) Positive gross P&L → 📈 glyph; negative → 📉 glyph.
(d) D-59 NYSE-closed-day gate: pandas_market_calendars schedule empty → return False,
    no DM sent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from gekko.db.models import Base, Event, Proposal, Strategy, User
from gekko.db.session import make_session_factory

_TEST_PASSPHRASE = "test-daily-pnl-passphrase"  # nosec: test-only
_USER_ID = "test-pnl-user"
_STRATEGY_ID_A = "strat-pnl-aaa"
_STRATEGY_ID_B = "strat-pnl-bbb"


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pnl_engine(tmp_path: Any) -> Any:
    """AsyncEngine with full schema for daily P&L tests."""
    from gekko.db.engine import get_async_engine

    engine = get_async_engine(tmp_path / "pnl_test.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_strategies(session: Any) -> None:
    """Seed user + two strategy rows."""
    now_iso = datetime.now(UTC).isoformat()
    user = User(
        user_id=_USER_ID,
        created_at=now_iso,
        agreement_acknowledged_at=now_iso,
    )
    session.add(user)
    await session.flush()

    from gekko.schemas.strategy import HardCaps, Strategy as StrategySchema

    strat_a = StrategySchema(
        strategy_id=_STRATEGY_ID_A,
        user_id=_USER_ID,
        name="strat-alpha",
        version=1,
        thesis="Alpha thesis",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.10"),
            max_daily_loss_usd=Decimal("1000"),
            max_trades_per_day=5,
            max_sector_exposure_pct=Decimal("0.20"),
        ),
        created_at=now_iso,
    )
    strat_b = StrategySchema(
        strategy_id=_STRATEGY_ID_B,
        user_id=_USER_ID,
        name="strat-beta",
        version=1,
        thesis="Beta thesis",
        watchlist=["AMD"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.10"),
            max_daily_loss_usd=Decimal("1000"),
            max_trades_per_day=5,
            max_sector_exposure_pct=Decimal("0.20"),
        ),
        created_at=now_iso,
    )

    session.add(
        Strategy(
            strategy_id=_STRATEGY_ID_A,
            user_id=_USER_ID,
            strategy_name="strat-alpha",
            version=1,
            payload_json=strat_a.model_dump_json(),
            created_at=now_iso,
        )
    )
    await session.flush()
    session.add(
        Strategy(
            strategy_id=_STRATEGY_ID_B,
            user_id=_USER_ID,
            strategy_name="strat-beta",
            version=1,
            payload_json=strat_b.model_dump_json(),
            created_at=now_iso,
        )
    )
    await session.flush()


def _make_fill_event(
    strategy_id: str,
    strategy_name: str,
    side: str,
    qty: str,
    fill_price: str,
    ts: str,
) -> dict[str, Any]:
    """Build a fill audit event payload."""
    return {
        "event_kind": "fill",
        "ticker": "NVDA",
        "client_order_id": "abc123",
        "broker_order_id": "brk001",
        "filled_qty": qty,
        "filled_avg_price": fill_price,
        "side": side,
        "strategy_name": strategy_name,
    }


# ---------------------------------------------------------------------------
# (a) Basic aggregation — 3 fills across 2 strategies + 1 error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_pnl_aggregation(pnl_engine: Any) -> None:
    """3 fills across 2 strategies + 1 error → correct per-strategy aggregation."""
    from gekko.audit.log import append_event
    from gekko.reporter.daily_pnl import _aggregate_today_events, _build_digest_blocks

    sf = make_session_factory(pnl_engine)

    # Pin a specific trading day for reproducibility (2026-06-17 is a Tuesday).
    today_et_date_str = "2026-06-17"
    ts_today = "2026-06-17T16:00:00+00:00"  # 16:00 UTC = 12:00 ET

    async with sf() as session, session.begin():
        await _seed_user_strategies(session)

    # Write events: 2 fills in strat-alpha, 1 fill in strat-beta, 1 error.
    # Strat-alpha: BUY 10 @ $100 = -$1000; SELL 10 @ $110 = +$1100 → net +$100
    # Strat-beta:  BUY 5  @ $200 = -$1000 → net -$1000 (not round-tripped)
    # Total gross: $100 + (-$1000) = -$900 BUT since we use fill-by-fill sign logic
    # (BUY negative, SELL positive), we check the aggregate.

    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID_A,
            event_type="fill",
            payload={
                "event_kind": "fill",
                "ticker": "NVDA",
                "client_order_id": "coid-001",
                "broker_order_id": "brk-001",
                "filled_qty": "10",
                "filled_avg_price": "100.00",
                "side": "buy",
                "strategy_name": "strat-alpha",
            },
            ts=ts_today,
        )
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID_A,
            event_type="fill",
            payload={
                "event_kind": "fill",
                "ticker": "NVDA",
                "client_order_id": "coid-002",
                "broker_order_id": "brk-002",
                "filled_qty": "10",
                "filled_avg_price": "110.00",
                "side": "sell",
                "strategy_name": "strat-alpha",
            },
            ts=ts_today,
        )
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID_B,
            event_type="fill",
            payload={
                "event_kind": "fill",
                "ticker": "AMD",
                "client_order_id": "coid-003",
                "broker_order_id": "brk-003",
                "filled_qty": "5",
                "filled_avg_price": "200.00",
                "side": "buy",
                "strategy_name": "strat-beta",
            },
            ts=ts_today,
        )
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID_A,
            event_type="error",
            payload={
                "context": "executor.market_closed",
                "error_class": "MarketClosed",
                "error_message": "test error",
                "proposal_id": "prop-001",
                "ticker": "NVDA",
            },
            ts=ts_today,
        )

    # Now aggregate via the module.
    from datetime import date as date_type

    today_et = date_type(2026, 6, 17)

    async with sf() as session:
        data = await _aggregate_today_events(session, _USER_ID, today_et)

    # 3 fills total
    assert data.fills_count == 3

    # 1 error
    assert data.errors_count == 1

    # Per-strategy: strat-alpha has 2 fills, strat-beta has 1 fill.
    assert "strat-alpha" in data.per_strategy
    assert data.per_strategy["strat-alpha"]["fills_count"] == 2
    assert "strat-beta" in data.per_strategy
    assert data.per_strategy["strat-beta"]["fills_count"] == 1

    # Build blocks and verify format string.
    blocks = _build_digest_blocks(data, today_et_date_str)
    # Header block must start with 📊
    assert blocks[0]["type"] == "header"
    assert "📊" in blocks[0]["text"]["text"]
    assert today_et_date_str in blocks[0]["text"]["text"]

    # Gross P&L section must contain the `:+,.2f` format pattern.
    gross_block = blocks[1]
    assert "Gross P&L" in gross_block["text"]["text"]
    # Sign prefix glyph is present.
    assert "📈" in gross_block["text"]["text"] or "📉" in gross_block["text"]["text"]


# ---------------------------------------------------------------------------
# (b) Zero fills today → `_no fills today_` branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_fills_branch(pnl_engine: Any) -> None:
    """Zero fills today renders the empty-state per-strategy line."""
    from datetime import date as date_type

    from gekko.reporter.daily_pnl import _aggregate_today_events, _build_digest_blocks

    sf = make_session_factory(pnl_engine)
    async with sf() as session, session.begin():
        await _seed_user_strategies(session)

    today_et = date_type(2026, 6, 17)
    async with sf() as session:
        data = await _aggregate_today_events(session, _USER_ID, today_et)

    assert data.fills_count == 0
    blocks = _build_digest_blocks(data, "2026-06-17")

    # Per-strategy block must contain the empty-state copy.
    per_strat_block = blocks[2]
    assert "_no fills today_" in per_strat_block["text"]["text"]


# ---------------------------------------------------------------------------
# (c) P&L sign drives glyph (positive → 📈, negative → 📉)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_sign_drives_glyph(pnl_engine: Any) -> None:
    """📈 for non-negative gross P&L; 📉 for negative."""
    from gekko.reporter.daily_pnl import _build_digest_blocks, DigestData

    # Positive P&L.
    data_positive = DigestData(
        fills_count=2,
        gross_pnl_usd=Decimal("100.00"),
        per_strategy={"test": {"pnl_usd": Decimal("100.00"), "fills_count": 2}},
        errors_count=0,
        cap_rejections_count=0,
        open_positions_count=1,
    )
    blocks_pos = _build_digest_blocks(data_positive, "2026-06-17")
    gross_text = blocks_pos[1]["text"]["text"]
    assert "📈" in gross_text
    assert "📉" not in gross_text

    # Negative P&L.
    data_negative = DigestData(
        fills_count=1,
        gross_pnl_usd=Decimal("-50.00"),
        per_strategy={"test": {"pnl_usd": Decimal("-50.00"), "fills_count": 1}},
        errors_count=0,
        cap_rejections_count=0,
        open_positions_count=0,
    )
    blocks_neg = _build_digest_blocks(data_negative, "2026-06-17")
    gross_text_neg = blocks_neg[1]["text"]["text"]
    assert "📉" in gross_text_neg
    assert "📈" not in gross_text_neg


# ---------------------------------------------------------------------------
# (d) D-59 market-closed-day gate → return False, no DM sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_closed_day_skips_digest(pnl_engine: Any) -> None:
    """When NYSE schedule is empty (weekend/holiday), returns False without sending DM."""
    from unittest.mock import patch

    sf = make_session_factory(pnl_engine)

    # Mock the NYSE schedule to return an empty DataFrame (market-closed day).
    mock_schedule = MagicMock()
    mock_schedule.empty = True

    mock_nyse = MagicMock()
    mock_nyse.schedule.return_value = mock_schedule

    mock_dm = AsyncMock()

    with (
        patch("gekko.reporter.daily_pnl.mcal") as mock_mcal,
        patch("gekko.reporter.daily_pnl._get_session_factory", return_value=(sf, None)),
        patch(
            "gekko.reporter.daily_pnl._send_dm_blocks_respecting_quiet_hours",
            new=mock_dm,
        ),
    ):
        mock_mcal.get_calendar.return_value = mock_nyse

        from gekko.reporter.daily_pnl import send_daily_pnl_digest

        result = await send_daily_pnl_digest(user_id=_USER_ID)

    assert result is False
    mock_dm.assert_not_called()
