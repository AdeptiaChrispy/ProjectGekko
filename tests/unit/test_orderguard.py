"""OrderGuard unit tests — Plan 02-02 Task 1 + Task 2.

Covers the 6 BLOCK checks (universe, paper_live, kill_switch read-side,
market_hours, hard_caps, qty_price) shipped by plan 02-02. The PDT/T+1/
wash-sale checks land in plan 02-03 and add their own assertions to this
file when that plan runs.

Architectural tests in this module:

  * OrderGuard IS-A Brokerage (isinstance + ABC contract)
  * ``OrderGuard.place_order`` carries NO ``@retry`` decorator (Knight-Capital
    invariant — Pitfall 4 / EXEC-03)
  * ``orderguard.py`` + every ``checks/_*.py`` carry NO ``claude_agent_sdk``
    substring (Anti-Pattern 1 grep gate — extends Plan 01-08's executor gate)
  * ``OrderGuard.{name,supports_fractional,is_paper}`` mirror the wrapped
    broker
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.brokers.base import Brokerage, OrderRequest, OrderResult
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.execution.checks import (
    check_hard_caps,
    check_kill_switch,
    check_market_hours,
    check_paper_live_pairing,
    check_qty_price_sanity,
    check_universe,
)
from gekko.execution.orderguard import OrderGuard
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.strategy import HardCaps, Strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(
    *,
    watchlist: list[str] | None = None,
    mode: str = "paper",
    max_position_pct: Decimal = Decimal("0.10"),
    max_daily_loss_usd: Decimal = Decimal("1000"),
    max_trades_per_day: int = 10,
    max_sector_exposure_pct: Decimal = Decimal("0.40"),
) -> Strategy:
    return Strategy(
        strategy_id="strat-test",
        user_id="test-user",
        name="test-strategy",
        version=1,
        thesis="test thesis",
        watchlist=watchlist or ["NVDA", "AAPL"],
        hard_caps=HardCaps(
            max_position_pct=max_position_pct,
            max_daily_loss_usd=max_daily_loss_usd,
            max_trades_per_day=max_trades_per_day,
            max_sector_exposure_pct=max_sector_exposure_pct,
        ),
        mode=mode,  # type: ignore[arg-type]
        created_at=datetime.now(UTC).isoformat(),
    )


def _make_order_request(
    *,
    symbol: str = "NVDA",
    qty: Decimal = Decimal("5"),
    order_type: OrderType = OrderType.LIMIT,
    limit_price: Decimal | None = Decimal("100"),
    stop_price: Decimal | None = None,
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=TimeInForce.DAY,
        client_order_id="a" * 32,
    )


def _make_proposal(
    *, target_notional_usd: Decimal = Decimal("500")
) -> TradeProposal:
    return TradeProposal(
        user_id="test-user",
        strategy_name="test-strategy",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=target_notional_usd,
        order_type="limit",
        limit_price=Decimal("100"),
        rationale="test rationale",
        confidence=Decimal("0.75"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/q/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="last $100.00",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="beat earnings",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q filed",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="AMD",
                why_rejected="lower data-center exposure",
            ),
        ],
        client_order_id="a" * 32,
        account_mode="PAPER",
    )


def _mock_broker(
    *,
    is_paper: bool = True,
    equity: str = "100000",
    quote_ask: str = "100",
    positions: list[dict[str, Any]] | None = None,
) -> MagicMock:
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.supports_fractional = True
    broker.is_paper = is_paper
    broker.get_account = AsyncMock(
        return_value={"equity": equity, "buying_power": equity}
    )
    broker.get_positions = AsyncMock(return_value=positions or [])
    broker.get_quote = AsyncMock(return_value={"ask_price": quote_ask})
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="broker-x",
            client_order_id="a" * 32,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": "broker-x"},
        )
    )
    broker.get_order_by_client_order_id = AsyncMock(return_value=None)
    broker.cancel_order = AsyncMock(return_value=True)
    broker.health_check = AsyncMock(return_value=True)
    # No _client => sector lookup gracefully skips (best-effort per RESEARCH §1).
    broker._client = None
    return broker


# ---------------------------------------------------------------------------
# check_universe (Task 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_universe_rejects_ticker_not_in_watchlist() -> None:
    strategy = _make_strategy(watchlist=["AAPL", "NVDA"])
    req = _make_order_request(symbol="TSLA")
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_universe(req, strategy=strategy)
    assert exc_info.value.reject_code == "universe"
    assert "TSLA" in exc_info.value.reject_reason
    assert "AAPL" in exc_info.value.reject_reason or "NVDA" in exc_info.value.reject_reason


@pytest.mark.asyncio
async def test_universe_passes_when_ticker_in_watchlist() -> None:
    strategy = _make_strategy(watchlist=["AAPL", "NVDA"])
    req = _make_order_request(symbol="NVDA")
    # Should not raise.
    await check_universe(req, strategy=strategy)


# ---------------------------------------------------------------------------
# check_paper_live_pairing (Task 1)
# ---------------------------------------------------------------------------


def test_paper_live_paper_strategy_paper_broker_paper_account_passes() -> None:
    broker = _mock_broker(is_paper=True)
    # Should not raise.
    check_paper_live_pairing(
        broker=broker,
        strategy_mode="paper",
        account_mode="PAPER",
        user_id="test-user",
    )


def test_paper_live_live_strategy_live_broker_live_account_passes() -> None:
    broker = _mock_broker(is_paper=False)
    # Should not raise.
    check_paper_live_pairing(
        broker=broker,
        strategy_mode="live",
        account_mode="LIVE",
        user_id="test-user",
    )


def test_paper_live_paper_strategy_live_broker_rejects() -> None:
    broker = _mock_broker(is_paper=False)
    with pytest.raises(OrderGuardRejected) as exc_info:
        check_paper_live_pairing(
            broker=broker,
            strategy_mode="paper",
            account_mode="PAPER",
            user_id="test-user",
        )
    assert exc_info.value.reject_code == "paper_live_mismatch_broker"


def test_paper_live_live_strategy_paper_broker_rejects() -> None:
    broker = _mock_broker(is_paper=True)
    with pytest.raises(OrderGuardRejected) as exc_info:
        check_paper_live_pairing(
            broker=broker,
            strategy_mode="live",
            account_mode="LIVE",
            user_id="test-user",
        )
    assert exc_info.value.reject_code == "paper_live_mismatch_broker"


def test_paper_live_account_mode_drift_paper_strategy_live_account_rejects() -> None:
    broker = _mock_broker(is_paper=True)
    with pytest.raises(OrderGuardRejected) as exc_info:
        check_paper_live_pairing(
            broker=broker,
            strategy_mode="paper",
            account_mode="LIVE",
            user_id="test-user",
        )
    assert exc_info.value.reject_code == "paper_live_mismatch_account"


def test_paper_live_account_mode_drift_live_strategy_paper_account_rejects() -> None:
    broker = _mock_broker(is_paper=False)
    with pytest.raises(OrderGuardRejected) as exc_info:
        check_paper_live_pairing(
            broker=broker,
            strategy_mode="live",
            account_mode="PAPER",
            user_id="test-user",
        )
    assert exc_info.value.reject_code == "paper_live_mismatch_account"


# ---------------------------------------------------------------------------
# check_kill_switch (Task 1)
# ---------------------------------------------------------------------------


async def _seed_user(sf: Any, *, user_id: str = "test-user", kill_active: bool = False) -> None:
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=datetime.now(UTC).isoformat(),
                kill_active=kill_active,
            )
        )


@pytest.mark.asyncio
async def test_kill_switch_passes_when_kill_inactive(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, kill_active=False)

    from gekko.execution.checks import _kill_switch as ks_mod

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # Should not raise.
    await check_kill_switch("test-user")


@pytest.mark.asyncio
async def test_kill_switch_rejects_when_kill_active(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, kill_active=True)

    from gekko.execution.checks import _kill_switch as ks_mod

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_kill_switch("test-user")
    assert exc_info.value.reject_code == "kill_active"
    assert "Kill switch is ON" in exc_info.value.reject_reason


@pytest.mark.asyncio
async def test_kill_switch_disposes_engine_when_engine_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the ``finally: await engine.dispose()`` shape fires.

    The test seam returns ``(sf, engine)`` instead of ``(sf, None)`` and asserts
    the engine's ``dispose`` was awaited even on the passing path.
    """
    from gekko.execution.checks import _kill_switch as ks_mod

    # Empty session factory that returns a context-managed session with
    # an empty users table query (scalar_one_or_none() returns None).
    fake_session_cm = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    fake_sf = MagicMock(return_value=fake_session_cm)
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock(return_value=None)

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (fake_sf, fake_engine)
    )

    await check_kill_switch("test-user")
    fake_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# check_market_hours (Task 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_hours_rejects_when_market_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gekko.execution.checks import _market_hours as mh_mod

    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: False)
    req = _make_order_request()
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_market_hours(req)
    assert exc_info.value.reject_code == "market_closed"


@pytest.mark.asyncio
async def test_market_hours_passes_when_market_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gekko.execution.checks import _market_hours as mh_mod

    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: True)
    req = _make_order_request()
    # Should not raise.
    await check_market_hours(req)


# ---------------------------------------------------------------------------
# check_qty_price_sanity (Task 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qty_price_limit_zero_drift_passes() -> None:
    broker = _mock_broker()
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
    )
    # 100 × 100 = 10000 → 0% drift from 10000.
    await check_qty_price_sanity(
        req=req,
        target_notional_usd=Decimal("10000"),
        broker=broker,
    )


@pytest.mark.asyncio
async def test_qty_price_limit_large_drift_rejects() -> None:
    broker = _mock_broker()
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("90"),
    )
    # 100 × 90 = 9000 → 10% drift from 10000.
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_qty_price_sanity(
            req=req,
            target_notional_usd=Decimal("10000"),
            broker=broker,
        )
    assert exc_info.value.reject_code == "qty_price_drift"


@pytest.mark.asyncio
async def test_qty_price_limit_drift_at_2_percent_boundary() -> None:
    """At exactly 2% drift -> pass; just above -> reject."""
    broker = _mock_broker()
    # 100 × 102 = 10200 → drift = 200 / 10000 = 2.00% (exactly at boundary)
    req_at = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("102"),
    )
    # 2.00% is NOT > 2% — passes (strict >).
    await check_qty_price_sanity(
        req=req_at,
        target_notional_usd=Decimal("10000"),
        broker=broker,
    )

    # 100 × 102.01 = 10201 → drift = 201 / 10000 = 2.01% (just over)
    req_over = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("102.01"),
    )
    with pytest.raises(OrderGuardRejected):
        await check_qty_price_sanity(
            req=req_over,
            target_notional_usd=Decimal("10000"),
            broker=broker,
        )


@pytest.mark.asyncio
async def test_qty_price_market_uses_quote_ask_price() -> None:
    broker = _mock_broker(quote_ask="100")
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.MARKET,
        limit_price=None,
    )
    # 100 × 100 = 10000 -> 0% drift.
    await check_qty_price_sanity(
        req=req,
        target_notional_usd=Decimal("10000"),
        broker=broker,
    )


@pytest.mark.asyncio
async def test_qty_price_market_accepts_ap_key_shape() -> None:
    """Quote with the legacy ``ap`` key shape (alpaca-py v1 wire format)."""
    broker = MagicMock(spec=Brokerage)
    broker.is_paper = True
    broker.name = "alpaca"
    broker.supports_fractional = True
    broker.get_quote = AsyncMock(return_value={"ap": "100"})
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.MARKET,
        limit_price=None,
    )
    await check_qty_price_sanity(
        req=req,
        target_notional_usd=Decimal("10000"),
        broker=broker,
    )


@pytest.mark.asyncio
async def test_qty_price_stop_uses_stop_price() -> None:
    broker = _mock_broker()
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.STOP,
        limit_price=None,
        stop_price=Decimal("100"),
    )
    await check_qty_price_sanity(
        req=req,
        target_notional_usd=Decimal("10000"),
        broker=broker,
    )


@pytest.mark.asyncio
async def test_qty_price_missing_ref_price_rejects() -> None:
    """MARKET order with no ask in the quote -> ref_price_missing."""
    broker = MagicMock(spec=Brokerage)
    broker.is_paper = True
    broker.get_quote = AsyncMock(return_value={})
    req = _make_order_request(
        symbol="NVDA",
        qty=Decimal("100"),
        order_type=OrderType.MARKET,
        limit_price=None,
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_qty_price_sanity(
            req=req,
            target_notional_usd=Decimal("10000"),
            broker=broker,
        )
    assert exc_info.value.reject_code == "ref_price_missing"


# ---------------------------------------------------------------------------
# check_hard_caps (Task 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_caps_position_pct_rejects_when_over_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)

    from gekko.execution.checks import _hard_caps as hc_mod

    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    strategy = _make_strategy(max_position_pct=Decimal("0.05"))  # 5% cap
    broker = _mock_broker(equity="10000")  # account equity = $10k
    # Proposed: 100 × $100 = $10,000 = 100% of equity > 5% cap.
    req = _make_order_request(
        qty=Decimal("100"), limit_price=Decimal("100")
    )

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_hard_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "hard_cap_position_pct"


@pytest.mark.asyncio
async def test_hard_caps_position_pct_passes_at_exact_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)

    from gekko.execution.checks import _hard_caps as hc_mod

    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    strategy = _make_strategy(max_position_pct=Decimal("0.10"))  # 10% cap
    broker = _mock_broker(equity="10000")
    # Proposed: 10 × $100 = $1000 = 10% of equity = exactly cap.
    req = _make_order_request(qty=Decimal("10"), limit_price=Decimal("100"))
    # Should not raise (strict > check).
    await check_hard_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


@pytest.mark.asyncio
async def test_hard_caps_trades_per_day_rejects_when_over_count(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed 3 ``order_submitted`` events today; cap = 3 -> reject the 4th."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)

    # Seed 3 order_submitted events for today via the canonical audit writer.
    from gekko.audit.log import append_event

    async with sf() as session, session.begin():
        for i in range(3):
            await append_event(
                session,
                user_id="test-user",
                strategy_id=None,
                event_type="order_submitted",
                payload={"i": i},
            )

    from gekko.execution.checks import _hard_caps as hc_mod

    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    strategy = _make_strategy(max_trades_per_day=3)
    broker = _mock_broker(equity="100000")
    # Small position so position_pct doesn't fire first.
    req = _make_order_request(qty=Decimal("1"), limit_price=Decimal("10"))

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_hard_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "hard_cap_trades_per_day"


@pytest.mark.asyncio
async def test_hard_caps_daily_loss_rejects_when_cumulative_over_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed a ``fill`` event today with realized_pnl_usd loss >= cap."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)

    from gekko.audit.log import append_event

    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id="test-user",
            strategy_id=None,
            event_type="fill",
            payload={"realized_pnl_usd": "-600"},
        )

    from gekko.execution.checks import _hard_caps as hc_mod

    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    strategy = _make_strategy(max_daily_loss_usd=Decimal("500"))
    broker = _mock_broker(equity="100000")
    req = _make_order_request(qty=Decimal("1"), limit_price=Decimal("10"))

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_hard_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "hard_cap_daily_loss"


@pytest.mark.asyncio
async def test_hard_caps_passes_in_clean_state(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)

    from gekko.execution.checks import _hard_caps as hc_mod

    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    strategy = _make_strategy()
    broker = _mock_broker(equity="100000")
    req = _make_order_request(qty=Decimal("5"), limit_price=Decimal("100"))
    # Should not raise.
    await check_hard_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


# ---------------------------------------------------------------------------
# OrderGuard class — architectural invariants
# ---------------------------------------------------------------------------


def test_orderguard_is_brokerage_subclass() -> None:
    assert issubclass(OrderGuard, Brokerage)


def test_orderguard_place_order_has_zero_decorators() -> None:
    """Pitfall 4 / EXEC-03 — order POSTs must NEVER auto-retry.

    Asserts ``__wrapped__`` is absent from ``OrderGuard.place_order``. The
    presence of ``__wrapped__`` is the canonical signal of a ``functools.wraps``
    /tenacity decorator having been applied. Knight-Capital insurance.
    """
    assert not hasattr(OrderGuard.place_order, "__wrapped__"), (
        "OrderGuard.place_order must NOT carry a retry/wraps decorator "
        "(Knight-Capital invariant — Pitfall 4 / EXEC-03)."
    )


def test_orderguard_place_order_ast_zero_decorators() -> None:
    """Stronger AST-level assertion that the source has no decorators."""
    import ast

    import gekko.execution.orderguard as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "place_order"
        ):
            found = True
            assert node.decorator_list == [], (
                "OrderGuard.place_order has unexpected decorators: "
                f"{[ast.dump(d) for d in node.decorator_list]!r}"
            )
    assert found, "place_order method not found in OrderGuard source"


def test_orderguard_mirrors_wrapped_broker_class_attrs() -> None:
    broker = _mock_broker(is_paper=True)
    broker.name = "alpaca"
    broker.supports_fractional = True
    strategy = _make_strategy()
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
    )
    assert guard.name == broker.name
    assert guard.supports_fractional == broker.supports_fractional
    assert guard.is_paper == broker.is_paper


@pytest.mark.asyncio
async def test_orderguard_delegates_gets_to_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = _mock_broker()
    strategy = _make_strategy()
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
    )
    await guard.get_account()
    broker.get_account.assert_awaited_once()
    await guard.get_positions()
    broker.get_positions.assert_awaited_once()
    await guard.get_quote("NVDA")
    broker.get_quote.assert_awaited_once_with("NVDA")
    await guard.cancel_order("broker-x")
    broker.cancel_order.assert_awaited_once_with("broker-x")
    await guard.get_order_by_client_order_id("a" * 32)
    broker.get_order_by_client_order_id.assert_awaited_once_with("a" * 32)
    await guard.health_check()
    broker.health_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_orderguard_place_order_delegates_when_all_checks_pass(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path — OrderGuard runs every check, then calls the wrapped broker."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, kill_active=False)

    from gekko.execution.checks import _hard_caps as hc_mod
    from gekko.execution.checks import _kill_switch as ks_mod
    from gekko.execution.checks import _market_hours as mh_mod

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: True)

    strategy = _make_strategy(watchlist=["NVDA"])
    broker = _mock_broker(equity="100000")
    proposal = _make_proposal(target_notional_usd=Decimal("500"))
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
        proposal=proposal,
    )
    req = _make_order_request(qty=Decimal("5"), limit_price=Decimal("100"))
    result = await guard.place_order(req)
    assert result.broker_order_id == "broker-x"
    broker.place_order.assert_awaited_once_with(req)


# ---------------------------------------------------------------------------
# Anti-Pattern 1 grep gate — no claude_agent_sdk substring in orderguard.py
# or any checks/_*.py file (extends Plan 01-08's executor gate)
# ---------------------------------------------------------------------------


def test_orderguard_module_does_not_import_claude_agent_sdk() -> None:
    import gekko.execution.orderguard as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in src
    assert "from claude_agent_sdk" not in src


def test_checks_modules_do_not_import_claude_agent_sdk() -> None:
    """Every ``src/gekko/execution/checks/_*.py`` file is grep-gated."""
    repo_root = Path(__file__).resolve().parents[2]
    checks_dir = repo_root / "src" / "gekko" / "execution" / "checks"
    assert checks_dir.is_dir(), f"missing {checks_dir}"
    offenses: list[Path] = []
    for py in checks_dir.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        if "claude_agent_sdk" in src:
            offenses.append(py.relative_to(repo_root))
    assert not offenses, (
        "claude_agent_sdk substring leaked into execution/checks/: "
        f"{offenses!r}"
    )
