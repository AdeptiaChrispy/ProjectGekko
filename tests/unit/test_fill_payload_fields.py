"""Tests for fill_payload fields in on_fill_event — Plan 03-09 Task 1 (CR-02).

Behaviors tested:
1. on_fill_event with a persisted proposal that has strategy_name="momentum" and
   side="sell" -> fill audit event payload contains {"strategy_name": "momentum",
   "side": "sell"}
2. on_fill_event where tp_persisted is None (defensive path) -> fill audit event
   payload contains {"strategy_name": "", "side": ""}
3. _aggregate_today_events with fill events seeded with strategy_name="alpha" and
   side="sell" -> that strategy's P&L bucket is positive (SELL = cash in)
4. _aggregate_today_events with fill events seeded WITHOUT strategy_name -> strategy
   name defaults to "_unknown_" (guard the default)
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from gekko.db.models import Base, Event, Proposal, Strategy, User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

_TEST_PASSPHRASE = "test-fill-fields-passphrase"  # nosec: test-only
_USER_ID = "test-fill-fields-user"
_STRATEGY_ID = "strat-fill-fields-aaa"


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fill_engine(tmp_path: Any) -> Any:
    """AsyncEngine with full schema for fill-payload field tests."""
    from gekko.db.engine import get_async_engine

    engine = get_async_engine(tmp_path / "fill_fields_test.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(strategy_name: str = "momentum", side: str = "sell") -> TradeProposal:
    """Build a valid TradeProposal for seeding proposal rows."""
    return TradeProposal(
        user_id=_USER_ID,
        strategy_name=strategy_name,
        decision_id=uuid4().hex,
        ticker="NVDA",
        side=side,
        qty=Decimal("10"),
        target_notional_usd=Decimal("1000.00"),
        order_type="limit",
        limit_price=Decimal("100.00"),
        rationale="Test rationale for fill payload fields.",
        confidence=Decimal("0.75"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="NVDA last trade $100.00.",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="Strong earnings beat.",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="10-Q filed cleanly.",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="Hold and wait.",
                why_rejected="Momentum favors action now.",
            ),
        ],
        client_order_id="a" * 32,
        account_mode="PAPER",
    )


async def _seed_user_strategy_and_proposal(
    session_factory: Any,
    *,
    strategy_name: str = "momentum",
    side: str = "sell",
    payload_json: str | None = None,
) -> tuple[str, str]:
    """Seed user + strategy + proposal rows. Returns (proposal_id, client_order_id)."""
    now_iso = datetime.now(UTC).isoformat()
    # client_order_id must be exactly 32 chars to pass TradeProposal validation.
    client_order_id = uuid4().hex  # 32-char hex string
    proposal_id = uuid4().hex

    tp = _make_trade_proposal(strategy_name=strategy_name, side=side)
    tp = tp.model_copy(update={"client_order_id": client_order_id, "decision_id": proposal_id})

    async with session_factory() as session, session.begin():
        session.add(User(user_id=_USER_ID, created_at=now_iso, agreement_acknowledged_at=now_iso))
        await session.flush()

        from gekko.schemas.strategy import HardCaps, Strategy as StrategySchema

        strat_schema = StrategySchema(
            strategy_id=_STRATEGY_ID,
            user_id=_USER_ID,
            name=strategy_name,
            version=1,
            thesis="Test thesis",
            watchlist=["NVDA"],
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
                strategy_id=_STRATEGY_ID,
                user_id=_USER_ID,
                strategy_name=strategy_name,
                version=1,
                payload_json=strat_schema.model_dump_json(),
                created_at=now_iso,
            )
        )
        await session.flush()

        session.add(
            Proposal(
                proposal_id=proposal_id,
                user_id=_USER_ID,
                strategy_id=_STRATEGY_ID,
                client_order_id=client_order_id,
                status="EXECUTING",
                payload_json=payload_json if payload_json is not None else tp.model_dump_json(),
                account_mode="PAPER",
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        await session.flush()

    return proposal_id, client_order_id


# ---------------------------------------------------------------------------
# Test 1: on_fill_event with known strategy_name + side -> fill payload correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_fill_event_includes_strategy_name_and_side(fill_engine: Any) -> None:
    """fill audit event payload must contain strategy_name and side from tp_persisted."""
    from sqlalchemy import select

    sf = make_session_factory(fill_engine)

    _, client_order_id = await _seed_user_strategy_and_proposal(
        sf, strategy_name="momentum", side="sell"
    )

    with (
        patch(
            "gekko.execution.executor._get_session_factory",
            return_value=(sf, None),
        ),
        patch("gekko.execution.executor._send_slack_dm", new=AsyncMock()),
        patch("gekko.execution.executor._send_slack_dm_blocks", new=AsyncMock()),
    ):
        from gekko.execution.executor import on_fill_event

        await on_fill_event(
            {
                "client_order_id": client_order_id,
                "broker_order_id": "brk-test-001",
                "filled_qty": "10",
                "filled_avg_price": "100.00",
                "ticker": "NVDA",
            },
            user_id=_USER_ID,
        )

    # Read the fill event back and check payload.
    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(
                    Event.user_id == _USER_ID,
                    Event.event_type == "fill",
                )
            )
        ).scalars().all()

    assert len(events) == 1, f"Expected 1 fill event, got {len(events)}"

    outer = json.loads(events[0].payload_json)
    payload = outer.get("payload", outer)

    assert payload.get("strategy_name") == "momentum", (
        f"Expected strategy_name='momentum', got {payload.get('strategy_name')!r}. "
        "CR-02: on_fill_event must add strategy_name from tp_persisted to fill_payload."
    )
    assert payload.get("side") == "sell", (
        f"Expected side='sell', got {payload.get('side')!r}. "
        "CR-02: on_fill_event must add side from tp_persisted to fill_payload."
    )


# ---------------------------------------------------------------------------
# Test 2: on_fill_event where tp_persisted is None -> graceful empty strings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_fill_event_tp_persisted_none_graceful(fill_engine: Any) -> None:
    """When payload_json is malformed (tp_persisted=None), strategy_name and side default to ''."""
    from sqlalchemy import select

    sf = make_session_factory(fill_engine)

    # Seed proposal with broken payload_json so model_validate_json fails.
    _, client_order_id = await _seed_user_strategy_and_proposal(
        sf,
        strategy_name="momentum",
        side="sell",
        payload_json="NOT_VALID_JSON{{{",
    )

    with (
        patch(
            "gekko.execution.executor._get_session_factory",
            return_value=(sf, None),
        ),
        patch("gekko.execution.executor._send_slack_dm", new=AsyncMock()),
        patch("gekko.execution.executor._send_slack_dm_blocks", new=AsyncMock()),
    ):
        from gekko.execution.executor import on_fill_event

        await on_fill_event(
            {
                "client_order_id": client_order_id,
                "broker_order_id": "brk-broken-001",
                "filled_qty": "5",
                "filled_avg_price": "50.00",
            },
            user_id=_USER_ID,
        )

    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(
                    Event.user_id == _USER_ID,
                    Event.event_type == "fill",
                )
            )
        ).scalars().all()

    assert len(events) == 1

    outer = json.loads(events[0].payload_json)
    payload = outer.get("payload", outer)

    assert payload.get("strategy_name") == "", (
        f"Expected strategy_name='' for broken payload, got {payload.get('strategy_name')!r}"
    )
    assert payload.get("side") == "", (
        f"Expected side='' for broken payload, got {payload.get('side')!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: _aggregate_today_events with side="sell" -> positive P&L in bucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_sell_fill_is_positive_pnl(fill_engine: Any) -> None:
    """A fill with side='sell' must contribute POSITIVE P&L to its strategy bucket."""
    from datetime import date as date_type

    from gekko.audit.log import append_event
    from gekko.reporter.daily_pnl import _aggregate_today_events

    sf = make_session_factory(fill_engine)
    ts_today = "2026-06-17T16:00:00+00:00"
    today_et = date_type(2026, 6, 17)

    await _seed_user_strategy_and_proposal(sf, strategy_name="alpha", side="sell")

    # Seed a SELL fill with explicit strategy_name="alpha" and side="sell".
    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID,
            event_type="fill",
            payload={
                "event_kind": "fill",
                "ticker": "NVDA",
                "client_order_id": "coid-sell-001",
                "broker_order_id": "brk-sell-001",
                "filled_qty": "10",
                "filled_avg_price": "110.00",
                "side": "sell",
                "strategy_name": "alpha",
            },
            ts=ts_today,
        )

    async with sf() as session:
        data = await _aggregate_today_events(session, _USER_ID, today_et)

    # The strategy bucket must be keyed by the exact strategy_name, not "_unknown_".
    assert "alpha" in data.per_strategy, (
        f"Expected strategy bucket keyed 'alpha', got buckets: {list(data.per_strategy.keys())}. "
        "CR-02: aggregator must use strategy_name from the fill event payload."
    )
    assert "_unknown_" not in data.per_strategy, (
        "'_unknown_' bucket must not appear when strategy_name is seeded. "
        "CR-02: fix on_fill_event to include strategy_name."
    )

    alpha_pnl = data.per_strategy["alpha"]["pnl_usd"]
    assert alpha_pnl > Decimal("0"), (
        f"SELL fill must produce positive P&L; got {alpha_pnl}. "
        "CR-02: SELL = cash in (positive sign convention)."
    )


# ---------------------------------------------------------------------------
# Test 4: _aggregate_today_events without strategy_name -> defaults to "_unknown_"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_missing_strategy_name_defaults_to_unknown(fill_engine: Any) -> None:
    """Fill events with no strategy_name field must bucket under '_unknown_'."""
    from datetime import date as date_type

    from gekko.audit.log import append_event
    from gekko.reporter.daily_pnl import _aggregate_today_events

    sf = make_session_factory(fill_engine)
    ts_today = "2026-06-17T16:00:00+00:00"
    today_et = date_type(2026, 6, 17)

    await _seed_user_strategy_and_proposal(sf, strategy_name="momentum", side="buy")

    # Seed a fill with NO strategy_name key in the payload.
    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID,
            event_type="fill",
            payload={
                "event_kind": "fill",
                "ticker": "NVDA",
                "client_order_id": "coid-unknown-001",
                "broker_order_id": "brk-unknown-001",
                "filled_qty": "5",
                "filled_avg_price": "100.00",
                # deliberately omit "strategy_name" and "side"
            },
            ts=ts_today,
        )

    async with sf() as session:
        data = await _aggregate_today_events(session, _USER_ID, today_et)

    assert "_unknown_" in data.per_strategy, (
        f"Expected '_unknown_' bucket for fill with no strategy_name, got: {list(data.per_strategy.keys())}"
    )
