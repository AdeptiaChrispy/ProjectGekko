"""OrderGuard paper/live pairing tests — Plan 02-02 Task 1 (EXEC-05).

Exercises the three-way invariant ``strategy.mode ⇔ account_mode ⇔
broker.is_paper`` via the OrderGuard.place_order surface (integration-flavored
unit test — the check function tests live in test_orderguard.py; these tests
exercise it through the OrderGuard class).

Plan 02-06 will deepen this file with the fourth-axis credential-kind check
(``paper_live_mismatch_credential``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from gekko.brokers.base import Brokerage, OrderResult
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.execution.orderguard import OrderGuard
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.strategy import HardCaps, Strategy
from gekko.brokers.base import OrderRequest


def _make_strategy(mode: str = "paper") -> Strategy:
    return Strategy(
        strategy_id="strat-pl",
        user_id="test-user",
        name="paper-live-test",
        version=1,
        thesis="paper/live invariant test",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.10"),
            max_daily_loss_usd=Decimal("1000"),
            max_trades_per_day=10,
            max_sector_exposure_pct=Decimal("0.40"),
        ),
        mode=mode,  # type: ignore[arg-type]
        created_at=datetime.now(UTC).isoformat(),
    )


def _make_proposal(account_mode: str = "PAPER") -> TradeProposal:
    return TradeProposal(
        user_id="test-user",
        strategy_name="paper-live-test",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=Decimal("500"),
        order_type="limit",
        limit_price=Decimal("100"),
        rationale="paper/live invariant test",
        confidence=Decimal("0.5"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/q/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="$100",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/n/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="news",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(description="AMD", why_rejected="lower"),
        ],
        client_order_id="a" * 32,
        account_mode=account_mode,  # type: ignore[arg-type]
    )


def _make_order_request() -> OrderRequest:
    return OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        time_in_force=TimeInForce.DAY,
        client_order_id="a" * 32,
    )


def _make_broker(is_paper: bool) -> MagicMock:
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.supports_fractional = True
    broker.is_paper = is_paper
    broker.get_account = AsyncMock(
        return_value={"equity": "100000", "buying_power": "100000"}
    )
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_quote = AsyncMock(return_value={"ask_price": "100"})
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="b",
            client_order_id="a" * 32,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={},
        )
    )
    broker._client = None
    return broker


async def _seed_user(sf: Any) -> None:
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
                kill_active=False,
            )
        )


def _patch_seams(
    monkeypatch: pytest.MonkeyPatch, *, sf: Any
) -> None:
    from gekko.execution.checks import _capital_ceiling as cc_mod
    from gekko.execution.checks import _hard_caps as hc_mod
    from gekko.execution.checks import _kill_switch as ks_mod
    from gekko.execution.checks import _market_hours as mh_mod
    from gekko.execution.checks import _portfolio_caps as pc_mod

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    # Phase 5: portfolio caps + capital ceiling build a vault-backed session
    # eagerly on EVERY place_order. Point them at the same test factory the
    # kill-switch / hard-caps seams already use (mirrors the existing idiom).
    monkeypatch.setattr(
        pc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        cc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: True)


# ---------------------------------------------------------------------------
# Aligned three-way invariants (happy paths)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_paper_paper_three_way_invariant_passes(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=True)
    strategy = _make_strategy(mode="paper")
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
        proposal=_make_proposal(account_mode="PAPER"),
    )
    result = await guard.place_order(_make_order_request())
    assert result.broker_order_id == "b"


@pytest.mark.asyncio
async def test_live_live_live_three_way_invariant_passes(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=False)
    strategy = _make_strategy(mode="live")
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="LIVE",
        user_id="test-user",
        proposal=_make_proposal(account_mode="LIVE"),
    )
    result = await guard.place_order(_make_order_request())
    assert result.broker_order_id == "b"


# ---------------------------------------------------------------------------
# Mismatch cases — every disagreement combination raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_strategy_live_broker_rejects(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=False)  # live broker
    strategy = _make_strategy(mode="paper")  # paper strategy
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
        proposal=_make_proposal(account_mode="PAPER"),
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await guard.place_order(_make_order_request())
    assert exc_info.value.reject_code == "paper_live_mismatch_broker"


@pytest.mark.asyncio
async def test_live_strategy_paper_broker_rejects(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=True)  # paper broker
    strategy = _make_strategy(mode="live")  # live strategy
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="LIVE",
        user_id="test-user",
        proposal=_make_proposal(account_mode="LIVE"),
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await guard.place_order(_make_order_request())
    assert exc_info.value.reject_code == "paper_live_mismatch_broker"


@pytest.mark.asyncio
async def test_paper_strategy_paper_broker_but_live_account_mode_rejects(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Account-mode drift — proposal stamped LIVE but strategy is paper."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=True)
    strategy = _make_strategy(mode="paper")
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="LIVE",  # drift!
        user_id="test-user",
        proposal=_make_proposal(account_mode="LIVE"),
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await guard.place_order(_make_order_request())
    assert exc_info.value.reject_code == "paper_live_mismatch_account"


@pytest.mark.asyncio
async def test_live_strategy_live_broker_but_paper_account_mode_rejects(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Account-mode drift — proposal stamped PAPER but strategy is live."""
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user(sf)
    _patch_seams(monkeypatch, sf=sf)

    broker = _make_broker(is_paper=False)
    strategy = _make_strategy(mode="live")
    guard = OrderGuard(
        broker,
        strategy=strategy,
        account_mode="PAPER",  # drift!
        user_id="test-user",
        proposal=_make_proposal(account_mode="PAPER"),
    )
    with pytest.raises(OrderGuardRejected) as exc_info:
        await guard.place_order(_make_order_request())
    assert exc_info.value.reject_code == "paper_live_mismatch_account"
