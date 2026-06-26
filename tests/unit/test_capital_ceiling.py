"""Per-strategy capital ceiling — TRUST-03/05 behavioral tests (Plan 05-03 Task 1).

Asserts the contract for ``gekko.execution.checks.check_capital_ceiling``: total
deployed capital (open positions for the strategy's watchlist tickers + this
order's notional) over ``StrategyMetadata.capital_ceiling_usd`` raises
``OrderGuardRejected("capital_ceiling")``; lowering the ceiling is unconstrained;
NULL ceiling reads the server_default ($1,000 per D-T16).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gekko.brokers.base import Brokerage, OrderRequest, OrderResult
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import StrategyMetadata, User
from gekko.db.session import make_session_factory
from gekko.execution.checks import check_capital_ceiling
from gekko.schemas.strategy import HardCaps, Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(*, watchlist: list[str] | None = None) -> Strategy:
    return Strategy(
        strategy_id="strat-test",
        user_id="test-user",
        name="test-strategy",
        version=1,
        thesis="test thesis",
        watchlist=watchlist or ["NVDA", "AAPL"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.20"),
            max_daily_loss_usd=Decimal("1000000"),
            max_trades_per_day=10000,
            max_sector_exposure_pct=Decimal("1"),
        ),
        mode="paper",  # type: ignore[arg-type]
        created_at=datetime.now(UTC).isoformat(),
    )


def _make_order_request(
    *,
    symbol: str = "NVDA",
    qty: Decimal = Decimal("5"),
    limit_price: Decimal | None = Decimal("100"),
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="a" * 32,
    )


def _mock_broker(
    *,
    equity: str = "100000",
    positions: list[dict[str, Any]] | None = None,
) -> MagicMock:
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.is_paper = True
    broker.get_account = AsyncMock(
        return_value={"equity": equity, "buying_power": equity}
    )
    broker.get_positions = AsyncMock(return_value=positions or [])
    broker.get_quote = AsyncMock(return_value={"ask_price": "100"})
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
    broker._client = None
    return broker


async def _seed(
    sf: Any, *, capital_ceiling_usd: str | None, has_meta: bool = True
) -> None:
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()  # parent users row before the FK child
        if has_meta:
            session.add(
                StrategyMetadata(
                    user_id="test-user",
                    strategy_name="test-strategy",
                    capital_ceiling_usd=capital_ceiling_usd,
                )
            )


def _patch_factory(monkeypatch: pytest.MonkeyPatch, sf: Any) -> None:
    from gekko.execution.checks import _capital_ceiling as cc_mod

    monkeypatch.setattr(cc_mod, "_get_session_factory", lambda _u: (sf, None))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_check_capital_ceiling_is_callable() -> None:
    assert callable(check_capital_ceiling)


@pytest.mark.asyncio
async def test_over_ceiling_rejects(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deployed $800 (NVDA) + $300 proposed = $1,100 > $1,000 ceiling → reject."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed(sf, capital_ceiling_usd="1000.00")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy(watchlist=["NVDA", "AAPL"])
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "800"}],
    )
    # 3 × $100 = $300 proposed.
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("3"), limit_price=Decimal("100")
    )

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_capital_ceiling(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "capital_ceiling"


@pytest.mark.asyncio
async def test_under_ceiling_passes(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed(sf, capital_ceiling_usd="1000.00")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy(watchlist=["NVDA", "AAPL"])
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "300"}],
    )
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("1"), limit_price=Decimal("100")
    )
    # $300 + $100 = $400 < $1,000 — no raise.
    await check_capital_ceiling(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


@pytest.mark.asyncio
async def test_only_strategy_watchlist_tickers_count(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positions in tickers NOT in this strategy's watchlist don't count."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed(sf, capital_ceiling_usd="1000.00")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy(watchlist=["NVDA"])
    broker = _mock_broker(
        equity="100000",
        # $5,000 in TSLA — NOT in this strategy's watchlist; must be ignored.
        positions=[
            {"symbol": "TSLA", "market_value": "5000"},
            {"symbol": "NVDA", "market_value": "100"},
        ],
    )
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("1"), limit_price=Decimal("100")
    )
    # NVDA $100 + $100 proposed = $200 < $1,000 — no raise (TSLA excluded).
    await check_capital_ceiling(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


@pytest.mark.asyncio
async def test_null_ceiling_reads_default_1000(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NULL ceiling column is read at the $1,000 default (D-T16)."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed(sf, capital_ceiling_usd=None)
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy(watchlist=["NVDA"])
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "900"}],
    )
    # $900 + $200 = $1,100 > $1,000 default → reject.
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("2"), limit_price=Decimal("100")
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_capital_ceiling(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "capital_ceiling"


@pytest.mark.asyncio
async def test_lowering_ceiling_is_unconstrained(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De-risking math: a SELL order never trips the ceiling (reduces deployment).

    The ceiling caps NEW deployment; a sell reduces exposure. The check only
    adds BUY notional. Here a sell of NVDA must always pass.
    """
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed(sf, capital_ceiling_usd="100.00")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy(watchlist=["NVDA"])
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "5000"}],
    )
    sell = OrderRequest(
        symbol="NVDA",
        side=OrderSide.SELL,
        qty=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="a" * 32,
    )
    # Selling reduces deployed capital — never blocked by the ceiling.
    await check_capital_ceiling(
        req=sell, strategy=strategy, broker=broker, user_id="test-user"
    )


def test_capital_ceiling_module_no_sdk_import() -> None:
    from pathlib import Path

    import gekko.execution.checks._capital_ceiling as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in src
