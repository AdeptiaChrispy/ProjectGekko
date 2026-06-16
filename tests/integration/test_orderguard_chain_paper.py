"""OrderGuard PAPER-path happy chain — Plan 02-02 Task 3 (D-26 / EXEC-04 audit).

Validates that wrapping every paper trade in OrderGuard is INVISIBLE to the
happy path. The Phase-1 walking-skeleton 5-event chain (proposal, approval,
order_submitted, fill) extends to STILL be 4 events here (we seed proposal
+ approval, then execute -> order_submitted; on_fill_event lands fill).
All 6 BLOCK checks pass for this proposal -> place_order delegates to the
wrapped broker normally.

Per plan 02-02 done criteria: the wrapped broker's place_order IS awaited
when all checks pass.
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


def _make_trade_proposal(*, user_id: str, decision_id: str) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="og-paper-happy",
        decision_id=decision_id,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=Decimal("500"),
        order_type="limit",
        limit_price=Decimal("100"),
        rationale="OrderGuard paper-path happy chain",
        confidence=Decimal("0.75"),
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
        account_mode="PAPER",
    )


def _make_strategy() -> Strategy:
    return Strategy(
        strategy_id="strat-og-happy",
        user_id="test-user",
        name="og-paper-happy",
        version=1,
        thesis="happy path through all 6 checks",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.20"),
            max_daily_loss_usd=Decimal("10000"),
            max_trades_per_day=50,
            max_sector_exposure_pct=Decimal("1"),
        ),
        mode="paper",
        created_at=datetime.now(UTC).isoformat(),
    )


@pytest.mark.asyncio
async def test_orderguard_paper_path_4_event_chain_intact(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: OrderGuard runs every check, all pass, broker is hit.

    Chain after the run: ``[proposal, approval, order_submitted]`` —
    fill comes from a separate ``on_fill_event`` callback which is
    covered by the Phase-1 walking-skeleton integration test
    (``test_trigger_run_end_to_end.py`` + ``test_slack_approval_to_executor.py``).
    """
    from gekko.audit import log as _audit_log
    from gekko.execution import executor
    from gekko.execution.checks import _hard_caps as hc_mod
    from gekko.execution.checks import _kill_switch as ks_mod
    from gekko.execution.checks import _market_hours as mh_mod
    from gekko.execution.orderguard import OrderGuard

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # Seed User + Strategy + APPROVED proposal + the [proposal, approval]
    # events so the chain starts at approval and execute_proposal walks
    # straight into OrderGuard.
    decision_id = uuid4().hex
    tp = _make_trade_proposal(user_id=user_id, decision_id=decision_id)
    strategy = _make_strategy()
    proposal_id = decision_id
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(user_id=user_id, created_at=now, kill_active=False)
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
                account_mode="PAPER",
            )
        )
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

    # Wire all the seams onto sf + a paper-ok MagicMock broker.
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.supports_fractional = True
    broker.is_paper = True
    broker.get_account = AsyncMock(
        return_value={"equity": "100000", "buying_power": "100000"}
    )
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_quote = AsyncMock(return_value={"ask_price": "100"})
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="broker-happy-001",
            client_order_id=tp.client_order_id,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": "broker-happy-001"},
        )
    )
    broker.health_check = AsyncMock(return_value=True)
    broker.get_order_by_client_order_id = AsyncMock(return_value=None)
    broker.cancel_order = AsyncMock(return_value=True)
    broker._client = None

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: True)

    def _fake_build_broker(
        user_id_: str,
        strategy_arg: Strategy,
        account_mode: str,
        *,
        proposal: TradeProposal | None = None,
    ) -> Any:
        return OrderGuard(
            broker,
            strategy=strategy_arg,
            account_mode=account_mode,  # type: ignore[arg-type]
            user_id=user_id_,
            proposal=proposal,
        )

    monkeypatch.setattr(executor, "_build_broker", _fake_build_broker)

    # ---- Run the executor ------------------------------------------------
    await executor.execute_proposal(proposal_id, user_id)

    # Wrapped broker WAS hit (all 6 OrderGuard checks passed).
    broker.place_order.assert_awaited_once()

    # ---- Assert chain shape + integrity ---------------------------------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        # OrderGuard PASSED -> executor proceeded to order_submitted ->
        # transition to EXECUTING.
        assert row.status == "EXECUTING"
        assert row.broker_order_id == "broker-happy-001"

        events = (
            await session.execute(
                select(Event).order_by(Event.id.asc())
            )
        ).scalars().all()
        types = [e.event_type for e in events]
        assert types == ["proposal", "approval", "order_submitted"], (
            f"unexpected event chain: {types}"
        )

        # No cap_rejection events.
        cap_count = sum(1 for e in events if e.event_type == "cap_rejection")
        assert cap_count == 0

        # Hash chain intact.
        broken = await walk_chain(session, user_id)
        assert broken == []
