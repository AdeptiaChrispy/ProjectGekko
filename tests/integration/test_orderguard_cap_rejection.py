"""OrderGuard cap_rejection end-to-end — Plan 02-02 Task 3 (D-30 / EXEC-04).

Full pipeline:

    PENDING proposal seeded
       -> approve_proposal (PENDING -> APPROVED + 'approval' event)
       -> execute_proposal:
            - load tp + strategy
            - market-hours guard passes (monkeypatched True)
            - _build_broker returns OrderGuard wrapping a MagicMock broker
            - OrderGuard.place_order runs the 6 BLOCK checks; one rejects
              -> OrderGuardRejected raised
            - cap_rejection branch in execute_proposal catches it:
                * append_event(event_type='cap_rejection', payload=...)
                * transition_status(APPROVED, FAILED)
            - broker.place_order on the WRAPPED concrete broker is NEVER
              awaited (OrderGuard raises BEFORE delegating)

Five rejection scenarios — one per check_name:

  1. universe (ticker not in strategy.watchlist)
  2. hard_cap_position_pct (small account equity)
  3. qty_price_drift (10% LIMIT price drift)
  4. paper_live_mismatch_broker (paper strategy + live broker)
  5. kill_active (users.kill_active = True in seeded DB)

After each rejection: assert the 4-event chain
``[proposal, approval, cap_rejection]`` walks intact (walk_chain == []).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.brokers.base import Brokerage, OrderResult
from gekko.db.models import Event, Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.strategy import HardCaps, Strategy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test fixtures + seed helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(
    *,
    ticker: str = "NVDA",
    qty: Decimal = Decimal("5"),
    target_notional_usd: Decimal = Decimal("500"),
    limit_price: Decimal = Decimal("100"),
    account_mode: str = "PAPER",
    user_id: str = "test-user",
    decision_id: str | None = None,
) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="og-test",
        decision_id=decision_id or uuid4().hex,
        ticker=ticker,
        side="buy",
        qty=qty,
        target_notional_usd=target_notional_usd,
        order_type="limit",
        limit_price=limit_price,
        rationale="cap-rejection test rationale",
        confidence=Decimal("0.75"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/q/" + ticker,
                fetched_at="2026-06-08T11:30:00+00:00",
                summary=f"{ticker} last ${limit_price}",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/n/" + ticker,
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="news",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/" + ticker,
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(description="alt", why_rejected="too risky"),
        ],
        client_order_id="a" * 32,
        account_mode=account_mode,  # type: ignore[arg-type]
    )


def _make_strategy(
    *,
    watchlist: list[str] | None = None,
    mode: str = "paper",
    user_id: str = "test-user",
    max_position_pct: Decimal = Decimal("0.10"),
) -> Strategy:
    return Strategy(
        strategy_id="strat-og",
        user_id=user_id,
        name="og-test",
        version=1,
        thesis="cap rejection scenarios",
        watchlist=watchlist or ["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=max_position_pct,
            max_daily_loss_usd=Decimal("1000"),
            max_trades_per_day=10,
            max_sector_exposure_pct=Decimal("0.40"),
        ),
        mode=mode,  # type: ignore[arg-type]
        created_at=datetime.now(UTC).isoformat(),
    )


async def _seed_chain(
    sf: Any,
    *,
    tp: TradeProposal,
    strategy: Strategy,
    user_id: str,
    kill_active: bool = False,
) -> str:
    """Seed User + Strategy + APPROVED Proposal + the [proposal, approval] events.

    Returns the proposal_id. The chain starts at APPROVED (PENDING ->
    APPROVED transition is captured in the approval event) so execute_proposal
    walks straight to OrderGuard.
    """
    proposal_id = tp.decision_id  # convention: decision_id == proposal_id
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=kill_active,
            )
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=user_id,
                strategy_name=strategy.name,
                version=1,
                payload_json=strategy.model_dump_json(),
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy.strategy_id,
                status="APPROVED",
                payload_json=tp.model_dump_json(),
                client_order_id=tp.client_order_id,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode=tp.account_mode,
            )
        )
        # Seed the two preceding events so the chain ends at 'approval'
        # and cap_rejection appears at id=3.
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy.strategy_id,
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy.strategy_id,
            event_type="approval",
            payload={
                "proposal_id": proposal_id,
                "actor": "test-actor",
                "slack_action_id": "approve_proposal",
            },
        )
    return proposal_id


def _success_broker(*, is_paper: bool = True) -> MagicMock:
    """A broker whose place_order returns a normal OrderResult.

    Used when the rejection should come from a check OTHER than the broker
    itself (universe, hard_caps, qty_price_drift). For the
    paper_live_mismatch_broker scenario, ``is_paper=False`` so the check
    fires.
    """
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
            broker_order_id="should-not-fire",
            client_order_id="a" * 32,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={},
        )
    )
    broker.health_check = AsyncMock(return_value=True)
    broker.get_order_by_client_order_id = AsyncMock(return_value=None)
    broker.cancel_order = AsyncMock(return_value=True)
    broker._client = None
    return broker


def _patch_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sf: Any,
    broker: MagicMock,
    strategy: Strategy,
    proposal: TradeProposal,
    market_open: bool = True,
) -> None:
    """Wire executor + checks seams onto a single in-memory engine + broker."""
    from gekko.execution import executor
    from gekko.execution.checks import _hard_caps as hc_mod
    from gekko.execution.checks import _kill_switch as ks_mod
    from gekko.execution.checks import _market_hours as mh_mod
    from gekko.execution.orderguard import OrderGuard

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: market_open)
    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: market_open)

    def _fake_build_broker(
        user_id: str,
        strategy_arg: Strategy,
        account_mode: str,
        *,
        proposal: TradeProposal | None = None,
    ) -> Any:
        return OrderGuard(
            broker,
            strategy=strategy_arg,
            account_mode=account_mode,  # type: ignore[arg-type]
            user_id=user_id,
            proposal=proposal,
        )

    monkeypatch.setattr(executor, "_build_broker", _fake_build_broker)


async def _assert_cap_rejection_chain(
    sf: Any,
    *,
    user_id: str,
    proposal_id: str,
    expected_reject_code: str,
) -> Event:
    """Assert the [proposal, approval, cap_rejection] chain + final FAILED state.

    Returns the cap_rejection event row for further payload assertions.
    """
    async with sf() as session:
        # Status should be FAILED after cap_rejection branch.
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        assert row.status == "FAILED", (
            f"expected FAILED after cap_rejection, found {row.status!r}"
        )

        # Event order.
        events = (
            await session.execute(
                select(Event).order_by(Event.id.asc())
            )
        ).scalars().all()
        types = [e.event_type for e in events]
        assert types == ["proposal", "approval", "cap_rejection"], (
            f"unexpected event chain: {types}"
        )

        cap = events[2]
        assert expected_reject_code in cap.payload_json, (
            f"expected reject_code {expected_reject_code!r} in payload, "
            f"got {cap.payload_json!r}"
        )

        # Hash chain intact.
        broken = await walk_chain(session, user_id)
        assert broken == []

        return cap


# ---------------------------------------------------------------------------
# 1. Universe rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_rejection_universe_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # Proposal: TSLA, strategy.watchlist: NVDA only -> universe rejects.
    tp = _make_trade_proposal(ticker="TSLA")
    strategy = _make_strategy(watchlist=["NVDA"])
    proposal_id = await _seed_chain(
        sf, tp=tp, strategy=strategy, user_id=user_id
    )

    broker = _success_broker(is_paper=True)
    _patch_seams(
        monkeypatch, sf=sf, broker=broker, strategy=strategy, proposal=tp
    )

    await executor.execute_proposal(proposal_id, user_id)

    # The wrapped broker's place_order MUST NOT have been called.
    broker.place_order.assert_not_awaited()

    cap = await _assert_cap_rejection_chain(
        sf,
        user_id=user_id,
        proposal_id=proposal_id,
        expected_reject_code="universe",
    )
    assert "TSLA" in cap.payload_json
    # The check_name is reject_code by convention.
    assert "check_name" in cap.payload_json


# ---------------------------------------------------------------------------
# 2. Hard-cap position_pct rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_rejection_hard_cap_position_pct_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # Position = 100 × $100 = $10k; equity will be $1k -> 1000% > 5% cap.
    tp = _make_trade_proposal(
        ticker="NVDA",
        qty=Decimal("100"),
        limit_price=Decimal("100"),
        target_notional_usd=Decimal("10000"),
    )
    strategy = _make_strategy(
        watchlist=["NVDA"], max_position_pct=Decimal("0.05")
    )
    proposal_id = await _seed_chain(
        sf, tp=tp, strategy=strategy, user_id=user_id
    )

    broker = _success_broker(is_paper=True)
    broker.get_account = AsyncMock(
        return_value={"equity": "1000", "buying_power": "1000"}
    )
    _patch_seams(
        monkeypatch, sf=sf, broker=broker, strategy=strategy, proposal=tp
    )

    await executor.execute_proposal(proposal_id, user_id)

    broker.place_order.assert_not_awaited()
    await _assert_cap_rejection_chain(
        sf,
        user_id=user_id,
        proposal_id=proposal_id,
        expected_reject_code="hard_cap_position_pct",
    )


# ---------------------------------------------------------------------------
# 3. Qty × price drift rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_rejection_qty_price_drift_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # qty × limit_price = 5 × $100 = $500; target_notional_usd = $1000.
    # Drift = abs(500-1000)/1000 = 50% > 2% -> qty_price_drift rejects.
    tp = _make_trade_proposal(
        ticker="NVDA",
        qty=Decimal("5"),
        limit_price=Decimal("100"),
        target_notional_usd=Decimal("1000"),
    )
    strategy = _make_strategy(watchlist=["NVDA"])
    proposal_id = await _seed_chain(
        sf, tp=tp, strategy=strategy, user_id=user_id
    )

    broker = _success_broker(is_paper=True)
    _patch_seams(
        monkeypatch, sf=sf, broker=broker, strategy=strategy, proposal=tp
    )

    await executor.execute_proposal(proposal_id, user_id)

    broker.place_order.assert_not_awaited()
    cap = await _assert_cap_rejection_chain(
        sf,
        user_id=user_id,
        proposal_id=proposal_id,
        expected_reject_code="qty_price_drift",
    )
    # The drift extras include the actual notional + target + drift pct.
    assert "actual_notional" in cap.payload_json


# ---------------------------------------------------------------------------
# 4. Paper/live mismatch broker rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_rejection_paper_live_mismatch_broker_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # paper strategy + paper account_mode but broker.is_paper=False -> mismatch.
    tp = _make_trade_proposal(ticker="NVDA")  # account_mode default PAPER
    strategy = _make_strategy(watchlist=["NVDA"], mode="paper")
    proposal_id = await _seed_chain(
        sf, tp=tp, strategy=strategy, user_id=user_id
    )

    broker = _success_broker(is_paper=False)  # mismatch
    _patch_seams(
        monkeypatch, sf=sf, broker=broker, strategy=strategy, proposal=tp
    )

    await executor.execute_proposal(proposal_id, user_id)

    broker.place_order.assert_not_awaited()
    await _assert_cap_rejection_chain(
        sf,
        user_id=user_id,
        proposal_id=proposal_id,
        expected_reject_code="paper_live_mismatch_broker",
    )


# ---------------------------------------------------------------------------
# 5. Kill-active rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_rejection_kill_active_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    tp = _make_trade_proposal(ticker="NVDA")
    strategy = _make_strategy(watchlist=["NVDA"])
    proposal_id = await _seed_chain(
        sf, tp=tp, strategy=strategy, user_id=user_id, kill_active=True
    )

    broker = _success_broker(is_paper=True)
    _patch_seams(
        monkeypatch, sf=sf, broker=broker, strategy=strategy, proposal=tp
    )

    await executor.execute_proposal(proposal_id, user_id)

    broker.place_order.assert_not_awaited()
    await _assert_cap_rejection_chain(
        sf,
        user_id=user_id,
        proposal_id=proposal_id,
        expected_reject_code="kill_active",
    )
