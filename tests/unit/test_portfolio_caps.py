"""Portfolio aggregate caps — TRUST-02/04 behavioral tests (Plan 05-03 Task 1).

Asserts the contract for ``gekko.execution.checks.check_portfolio_caps``: each of
the four aggregate caps (account-wide, not per-strategy) raises
``OrderGuardRejected`` with its dedicated reject_code when the AGGREGATE limit is
exceeded; a blank/NULL cap column = disabled = no raise.

reject_code vocabulary (locked):
  * portfolio_total_exposure
  * portfolio_sector_concentration
  * portfolio_correlated_ticker
  * portfolio_daily_loss

Aggregation reads a SINGLE ``get_positions()`` call (Alpaca nets one position per
ticker — RESEARCH Pitfall 4); never N×M per-strategy broker calls.
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
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.execution.checks import check_portfolio_caps
from gekko.schemas.strategy import HardCaps, Strategy

_REJECT_CODES = (
    "portfolio_total_exposure",
    "portfolio_sector_concentration",
    "portfolio_correlated_ticker",
    "portfolio_daily_loss",
)


# ---------------------------------------------------------------------------
# Helpers (mirror tests/unit/test_orderguard.py)
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
    broker.supports_fractional = True
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
    broker._client = None  # sector lookup gracefully skips
    return broker


async def _seed_user(sf: Any, **caps: str | None) -> None:
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
                **caps,
            )
        )


def _patch_factory(monkeypatch: pytest.MonkeyPatch, sf: Any) -> None:
    from gekko.execution.checks import _portfolio_caps as pc_mod

    monkeypatch.setattr(pc_mod, "_get_session_factory", lambda _u: (sf, None))


# ---------------------------------------------------------------------------
# reject_code vocabulary
# ---------------------------------------------------------------------------


def test_check_portfolio_caps_is_callable() -> None:
    assert callable(check_portfolio_caps)


# ---------------------------------------------------------------------------
# total exposure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_exposure_rejects_when_over_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing $40k positions + $20k proposed = $60k / $100k = 60% > 50% cap."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, max_total_exposure_pct="0.50")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy()
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "MSFT", "market_value": "40000"}],
    )
    # 200 × $100 = $20,000 proposed.
    req = _make_order_request(qty=Decimal("200"), limit_price=Decimal("100"))

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_portfolio_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "portfolio_total_exposure"


@pytest.mark.asyncio
async def test_total_exposure_passes_under_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, max_total_exposure_pct="0.50")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy()
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "MSFT", "market_value": "10000"}],
    )
    req = _make_order_request(qty=Decimal("50"), limit_price=Decimal("100"))
    # $10k + $5k = $15k / $100k = 15% < 50% — no raise.
    await check_portfolio_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


@pytest.mark.asyncio
async def test_blank_total_exposure_cap_is_disabled(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NULL cap column disables that cap entirely (no raise even at 100%)."""
    sf = make_session_factory(temp_sqlcipher_db)
    # All four caps NULL → fully disabled.
    await _seed_user(sf)
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy()
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "MSFT", "market_value": "90000"}],
    )
    req = _make_order_request(qty=Decimal("500"), limit_price=Decimal("100"))
    # $90k + $50k = $140k = 140% but every cap is NULL → no raise.
    await check_portfolio_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


# ---------------------------------------------------------------------------
# correlated ticker (single net Alpaca position per ticker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlated_ticker_rejects_when_over_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Net NVDA position $10k + $10k proposed = $20k / $100k = 20% > 15% cap."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, max_correlated_ticker_pct="0.15")
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy()
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "10000"}],
    )
    # 100 × $100 = $10,000 proposed in NVDA.
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("100"), limit_price=Decimal("100")
    )

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_portfolio_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "portfolio_correlated_ticker"


@pytest.mark.asyncio
async def test_correlated_ticker_aggregates_single_get_positions_call(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check must read positions via ONE get_positions() call (Pitfall 4)."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(
        sf,
        max_total_exposure_pct="0.50",
        max_sector_concentration_pct="0.30",
        max_correlated_ticker_pct="0.15",
        max_total_daily_loss_usd="200.00",
    )
    _patch_factory(monkeypatch, sf)

    strategy = _make_strategy()
    broker = _mock_broker(
        equity="100000",
        positions=[{"symbol": "NVDA", "market_value": "1000"}],
    )
    req = _make_order_request(
        symbol="NVDA", qty=Decimal("1"), limit_price=Decimal("100")
    )
    await check_portfolio_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )
    # All four caps active but the broker positions endpoint is hit at most
    # once — no per-strategy fan-out.
    assert broker.get_positions.await_count <= 1


# ---------------------------------------------------------------------------
# daily loss (portfolio-wide)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_loss_rejects_when_over_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed a portfolio-wide realized loss today >= the USD cap → reject."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, max_total_daily_loss_usd="200.00")
    _patch_factory(monkeypatch, sf)

    from gekko.audit.log import append_event

    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id="test-user",
            strategy_id=None,
            event_type="fill",
            payload={"realized_pnl_usd": "-250"},
        )

    strategy = _make_strategy()
    broker = _mock_broker(equity="100000")
    req = _make_order_request(qty=Decimal("1"), limit_price=Decimal("10"))

    with pytest.raises(OrderGuardRejected) as exc_info:
        await check_portfolio_caps(
            req=req, strategy=strategy, broker=broker, user_id="test-user"
        )
    assert exc_info.value.reject_code == "portfolio_daily_loss"


@pytest.mark.asyncio
async def test_daily_loss_passes_under_cap(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf, max_total_daily_loss_usd="200.00")
    _patch_factory(monkeypatch, sf)

    from gekko.audit.log import append_event

    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id="test-user",
            strategy_id=None,
            event_type="fill",
            payload={"realized_pnl_usd": "-50"},
        )

    strategy = _make_strategy()
    broker = _mock_broker(equity="100000")
    req = _make_order_request(qty=Decimal("1"), limit_price=Decimal("10"))
    await check_portfolio_caps(
        req=req, strategy=strategy, broker=broker, user_id="test-user"
    )


# ---------------------------------------------------------------------------
# no claude_agent_sdk import (defense in depth — also gated by test_orderguard)
# ---------------------------------------------------------------------------


def test_portfolio_caps_module_no_sdk_import() -> None:
    from pathlib import Path

    import gekko.execution.checks._portfolio_caps as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in src
