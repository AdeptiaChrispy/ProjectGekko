"""PDT + T+1 detection tests — Plan 02-03 Task 2 (EXEC-11).

Covers the two regulatory BLOCK checks shipped by 02-03:

* ``check_pdt`` (Pattern Day Trader) — two sources:
    - Broker primary: ``pattern_day_trader=True`` + ``daytrade_count>=3``
      + ``equity<$25K`` -> ``OrderGuardRejected('pdt_rule')``
    - Local defense: 5-business-day round-trip count >= 3 AND
      ``equity<$25K`` AND this order completes a 4th round-trip ->
      ``OrderGuardRejected('pdt_rule_local')``

* ``check_t1_settlement`` (T+1 unsettled cash) — cash-account BUY whose
  cost exceeds ``non_marginable_buying_power`` ->
  ``OrderGuardRejected('t1_settlement')``. Margin accounts
  (``shorting_enabled=True``) are exempt. SELL is exempt.

Test seam: each check exposes a module-level ``_get_session_factory``
that tests monkeypatch with a pre-built ``(factory, None)`` tuple (the
None means "don't dispose the engine in finally" — the test owns the
lifecycle).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from gekko.audit.log import append_event
from gekko.brokers.base import OrderRequest
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.execution.checks._pdt import check_pdt
from gekko.execution.checks._t1 import check_t1_settlement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _buy_req(symbol: str = "AAPL", qty: str = "10") -> OrderRequest:
    """Construct a vanilla BUY LIMIT OrderRequest."""
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.00"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )


def _sell_req(symbol: str = "AAPL", qty: str = "10") -> OrderRequest:
    """Construct a vanilla SELL LIMIT OrderRequest."""
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.SELL,
        qty=Decimal(qty),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.00"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )


@pytest_asyncio.fixture
async def seeded_engine(temp_sqlcipher_db: Any) -> AsyncIterator[Any]:
    """Yield a SQLCipher engine with a seeded ``users`` row.

    Many PDT tests don't need user-row seeding (the broker-side path doesn't
    walk the DB), but the local-source tests need event seeding which
    requires a User FK row.
    """
    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    yield temp_sqlcipher_db


def _patch_session_factory(
    monkeypatch: pytest.MonkeyPatch, engine: Any, *, module: Any
) -> None:
    """Patch the module's ``_get_session_factory`` to return a pre-built sf.

    ``None`` for the engine ensures the production ``finally: engine.dispose()``
    is a no-op — the test owns the engine lifecycle via the fixture.
    """
    sf = make_session_factory(engine)
    monkeypatch.setattr(
        module,
        "_get_session_factory",
        lambda user_id: (sf, None),
        raising=True,
    )


# ===========================================================================
# PDT — broker source
# ===========================================================================


@pytest.mark.asyncio
async def test_pdt_broker_source_blocks_when_flagged_and_under_25k() -> None:
    """Account flagged PDT + daytrade_count >= 3 + equity < $25K -> BLOCK."""
    account = {
        "pattern_day_trader": True,
        "daytrade_count": "3",
        "equity": "10000",
    }
    req = _buy_req()
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_pdt(req=req, account=account, user_id="test-user")
    assert exc_info.value.reject_code == "pdt_rule"
    assert exc_info.value.extra["ticker"] == "AAPL"
    assert exc_info.value.extra["daytrade_count"] == 3
    assert exc_info.value.extra["equity"] == "10000"


@pytest.mark.asyncio
async def test_pdt_broker_source_passes_above_25k_equity() -> None:
    """Equity >= $25K — PDT rule does not bind. Passes regardless of flag."""
    account = {
        "pattern_day_trader": True,
        "daytrade_count": "5",
        "equity": "30000",
    }
    req = _buy_req()
    # No raise.
    await check_pdt(req=req, account=account, user_id="test-user")


async def _empty_async(value: Any) -> Any:
    """Return ``value`` from an awaited async function (helper for mocks)."""
    return value


@pytest.mark.asyncio
async def test_pdt_broker_source_passes_with_low_daytrade_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daytrade_count < 3 — 4th day-trade hasn't arrived yet. Passes."""
    from gekko.execution.checks import _pdt as pdt_mod

    # The broker-source branch does NOT fire (daytrade_count < 3) — but
    # equity is below threshold so the local-source branch still walks.
    # Stub the walk to return no fills.
    monkeypatch.setattr(
        pdt_mod, "_walk_fills_in_window", lambda *a, **kw: _empty_async([])
    )
    account = {
        "pattern_day_trader": True,
        "daytrade_count": "2",
        "equity": "10000",
    }
    req = _buy_req()
    # No raise.
    await check_pdt(req=req, account=account, user_id="test-user")


@pytest.mark.asyncio
async def test_pdt_broker_source_passes_when_flag_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pattern_day_trader=False`` — broker hasn't flagged yet. Local source
    still walks but with no events seeded, count is 0."""
    from gekko.execution.checks import _pdt as pdt_mod

    monkeypatch.setattr(
        pdt_mod, "_walk_fills_in_window", lambda *a, **kw: _empty_async([])
    )
    account = {
        "pattern_day_trader": False,
        "daytrade_count": "10",
        "equity": "10000",
    }
    req = _buy_req()
    # Local-source walk returns no fills -> count=0 -> passes.
    await check_pdt(req=req, account=account, user_id="test-user")


@pytest.mark.asyncio
async def test_pdt_handles_string_daytrade_count() -> None:
    """``daytrade_count`` arrives as a string per Alpaca SDK — coerced to int."""
    account = {
        "pattern_day_trader": True,
        "daytrade_count": "4",  # string, per alpaca-py
        "equity": "10000",
    }
    req = _buy_req()
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_pdt(req=req, account=account, user_id="test-user")
    assert exc_info.value.extra["daytrade_count"] == 4


@pytest.mark.asyncio
async def test_pdt_handles_missing_fields_gracefully() -> None:
    """Account dict with missing fields defaults to 0 / False / Decimal('0').

    Above-threshold equity short-circuits the local walk too.
    """
    account = {"equity": "100000"}  # missing pattern_day_trader + daytrade_count
    req = _buy_req()
    # No raise.
    await check_pdt(req=req, account=account, user_id="test-user")


# ===========================================================================
# PDT — local source (defense in depth)
# ===========================================================================


async def _seed_round_trips(
    engine: Any, user_id: str, *, count: int, ticker: str
) -> None:
    """Seed N round-trips (BUY+SELL same-day same-ticker) for ``user_id``.

    Each round-trip is on a distinct prior business day within the last
    5 business days. We use UTC dates 1, 2, 3, ... days ago (close
    enough — the test doesn't need exact NYSE calendar alignment because
    the rolling 5-business-day window starts from now and includes today).
    """
    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        for i in range(count):
            # Distinct day for each round-trip. 0=today, 1=yesterday, etc.
            day = (datetime.now(UTC) - timedelta(days=i + 1)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )
            buy_ts = day.isoformat()
            sell_ts = day.replace(hour=15).isoformat()
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="fill",
                payload={
                    "ticker": ticker,
                    "side": "buy",
                    "filled_qty": "10",
                    "ts": buy_ts,
                },
                ts=buy_ts,
            )
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="fill",
                payload={
                    "ticker": ticker,
                    "side": "sell",
                    "filled_qty": "10",
                    "ts": sell_ts,
                },
                ts=sell_ts,
            )


async def _seed_today_buy(
    engine: Any, user_id: str, *, ticker: str
) -> None:
    """Seed a today-BUY fill so a same-day SELL would complete a round-trip."""
    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        today = datetime.now(UTC).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=None,
            event_type="fill",
            payload={
                "ticker": ticker,
                "side": "buy",
                "filled_qty": "10",
                "ts": today.isoformat(),
            },
            ts=today.isoformat(),
        )


@pytest.mark.asyncio
async def test_pdt_local_source_blocks_after_3_round_trips_and_4th_today(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 prior round-trips + today's BUY + incoming SELL = 4th round-trip.

    With equity < $25K, BLOCK with ``pdt_rule_local``.
    """
    from gekko.execution.checks import _pdt as pdt_mod

    _patch_session_factory(monkeypatch, seeded_engine, module=pdt_mod)

    # Seed 3 round-trips on prior days + a BUY today.
    await _seed_round_trips(
        seeded_engine, "test-user", count=3, ticker="AAPL"
    )
    await _seed_today_buy(seeded_engine, "test-user", ticker="AAPL")

    account = {
        "pattern_day_trader": False,  # broker-side hasn't flagged yet
        "daytrade_count": "0",
        "equity": "10000",
    }
    # Incoming SELL on AAPL would complete today's 4th round-trip.
    req = _sell_req(symbol="AAPL")
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_pdt(req=req, account=account, user_id="test-user")
    assert exc_info.value.reject_code == "pdt_rule_local"
    assert exc_info.value.extra["local_round_trip_count"] >= 3


@pytest.mark.asyncio
async def test_pdt_local_source_passes_above_25k_equity(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above $25K equity short-circuits BEFORE the local walk."""
    from gekko.execution.checks import _pdt as pdt_mod

    _patch_session_factory(monkeypatch, seeded_engine, module=pdt_mod)
    await _seed_round_trips(
        seeded_engine, "test-user", count=5, ticker="AAPL"
    )

    account = {
        "pattern_day_trader": False,
        "daytrade_count": "0",
        "equity": "30000",
    }
    req = _sell_req(symbol="AAPL")
    # No raise — above threshold.
    await check_pdt(req=req, account=account, user_id="test-user")


@pytest.mark.asyncio
async def test_pdt_local_source_passes_with_only_2_round_trips(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only 2 prior round-trips + incoming 3rd = 3 total, below the 4-trade
    threshold. Passes."""
    from gekko.execution.checks import _pdt as pdt_mod

    _patch_session_factory(monkeypatch, seeded_engine, module=pdt_mod)
    await _seed_round_trips(
        seeded_engine, "test-user", count=2, ticker="AAPL"
    )
    await _seed_today_buy(seeded_engine, "test-user", ticker="AAPL")

    account = {
        "pattern_day_trader": False,
        "daytrade_count": "0",
        "equity": "10000",
    }
    req = _sell_req(symbol="AAPL")
    # No raise.
    await check_pdt(req=req, account=account, user_id="test-user")


@pytest.mark.asyncio
async def test_pdt_local_source_passes_when_not_round_trip(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 prior round-trips on AAPL, incoming MSFT BUY — different ticker, no
    same-day opposite-side match exists, so no 4th round-trip. Passes."""
    from gekko.execution.checks import _pdt as pdt_mod

    _patch_session_factory(monkeypatch, seeded_engine, module=pdt_mod)
    await _seed_round_trips(
        seeded_engine, "test-user", count=3, ticker="AAPL"
    )

    account = {
        "pattern_day_trader": False,
        "daytrade_count": "0",
        "equity": "10000",
    }
    req = _buy_req(symbol="MSFT")  # NEW ticker — not part of any round-trip
    # No raise — _would_be_round_trip returns False.
    await check_pdt(req=req, account=account, user_id="test-user")


# ===========================================================================
# T+1 settlement
# ===========================================================================


@pytest.mark.asyncio
async def test_t1_cash_account_blocks_when_cost_exceeds_settled_cash() -> None:
    """Cash account, BUY cost > non_marginable_buying_power -> BLOCK."""
    account = {
        "shorting_enabled": False,  # cash account
        "non_marginable_buying_power": "1000",
    }
    # 10 shares * $100 = $1000 ... but limit_price is $100 so cost = $1000.
    # We need cost > 1000, so increase qty.
    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("11"),  # 11 * 100 = 1100 > 1000
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_t1_settlement(req=req, account=account)
    assert exc_info.value.reject_code == "t1_settlement"
    assert exc_info.value.extra["ticker"] == "AAPL"
    assert exc_info.value.extra["order_cost"] == "1100"
    assert exc_info.value.extra["non_marginable_buying_power"] == "1000"


@pytest.mark.asyncio
async def test_t1_cash_account_passes_when_cost_within_settled_cash() -> None:
    """Cost <= non_marginable_buying_power -> pass."""
    account = {
        "shorting_enabled": False,
        "non_marginable_buying_power": "5000",
    }
    req = _buy_req()  # 10 * 100 = 1000 <= 5000
    # No raise.
    await check_t1_settlement(req=req, account=account)


@pytest.mark.asyncio
async def test_t1_margin_account_passes_regardless_of_settled_cash() -> None:
    """Margin account (``shorting_enabled=True``) — T+1 doesn't bind."""
    account = {
        "shorting_enabled": True,
        "non_marginable_buying_power": "10",  # tiny — would block on cash
    }
    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )
    # No raise — margin advances credit.
    await check_t1_settlement(req=req, account=account)


@pytest.mark.asyncio
async def test_t1_sell_side_exempt() -> None:
    """SELL is exempt — proceeds aren't being spent."""
    account = {
        "shorting_enabled": False,
        "non_marginable_buying_power": "0",
    }
    req = _sell_req()
    # No raise — SELL bypasses T+1.
    await check_t1_settlement(req=req, account=account)


@pytest.mark.asyncio
async def test_t1_market_order_uses_quote_ask_price() -> None:
    """MARKET order with no limit_price — fetches ask from broker.get_quote."""

    class _FakeBroker:
        async def get_quote(self, symbol: str) -> dict[str, Any]:
            return {"ask_price": "100"}

    account = {
        "shorting_enabled": False,
        "non_marginable_buying_power": "500",
    }
    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("10"),  # 10 * 100 = 1000 > 500
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_t1_settlement(
            req=req, account=account, broker=_FakeBroker()  # type: ignore[arg-type]
        )
    assert exc_info.value.reject_code == "t1_settlement"


@pytest.mark.asyncio
async def test_t1_market_order_uses_cached_quote_when_present() -> None:
    """Account dict's ``last_quote_ask`` short-circuits the broker call."""
    account = {
        "shorting_enabled": False,
        "non_marginable_buying_power": "500",
        "last_quote_ask": "100",
    }
    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("10"),
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_t1_settlement(req=req, account=account)
    assert exc_info.value.reject_code == "t1_settlement"


@pytest.mark.asyncio
async def test_t1_missing_non_marginable_field_passes() -> None:
    """When ``non_marginable_buying_power`` is absent, check defers."""
    account = {"shorting_enabled": False}  # no non_marginable field
    req = _buy_req()
    # No raise — nothing to compare against.
    await check_t1_settlement(req=req, account=account)


# ===========================================================================
# Imports + integration smoke
# ===========================================================================


def test_imports_resolve() -> None:
    """``from gekko.execution.checks import check_pdt, check_t1_settlement``
    works (re-exports landed)."""
    from gekko.execution.checks import check_pdt as cp
    from gekko.execution.checks import check_t1_settlement as ct1

    assert cp is check_pdt
    assert ct1 is check_t1_settlement
