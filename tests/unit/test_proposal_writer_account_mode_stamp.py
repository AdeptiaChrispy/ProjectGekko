"""BLOCKER #5 runtime half — ProposalWriter stamps account_mode at T0.

Plan 02-06 Task 2. The ProposalWriter reads
``strategy.mode`` + ``strategy_metadata.live_mode_eligible`` AT
PROPOSAL-BUILD TIME (T0) and stamps the resulting ``account_mode`` onto
the TradeProposal. Downstream callers (Slack approve handler, executor)
MUST read ``account_mode`` from the LOCKED proposal row, never re-derive
from strategy state at execute-time. This closes the TOCTOU window
between proposal-gen (T0) and approve-click (T1).

The decision rule:
  * paper strategy → "PAPER" (regardless of metadata)
  * live strategy AND live_mode_eligible=True → "LIVE"
  * live strategy AND no metadata row → "PAPER" (defensive default)
  * live strategy AND live_mode_eligible=False → "PAPER" (defensive)

TOCTOU defense: after the writer stamps "PAPER", promoting the strategy
to live MUST NOT change the already-persisted proposal's account_mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from gekko.agent import proposal_writer
from gekko.agent.proposal_writer import write_proposal
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import StrategyMetadata
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import TradeProposal
from gekko.schemas.strategy import HardCaps, Strategy


def _make_strategy(mode: str = "paper") -> Strategy:
    return Strategy(
        strategy_id="strat-stamp-test",
        user_id="test-user",
        name="stamp-test",
        version=1,
        thesis="account_mode stamp test",
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


def _trade_payload() -> dict[str, Any]:
    """LLM-supplied propose_trade kwargs (sans runtime fields)."""
    return {
        "ticker": "NVDA",
        "side": "buy",
        "qty": Decimal("5"),
        "target_notional_usd": Decimal("500"),
        "order_type": "limit",
        "limit_price": Decimal("100"),
        "rationale": "account_mode stamp test rationale",
        "confidence": Decimal("0.5"),
        "evidence": [
            {
                "source_type": "alpaca_quote",
                "source_url": "https://alpaca.markets/q/NVDA",
                "fetched_at": "2026-06-08T11:30:00+00:00",
                "summary": "$100",
            },
            {
                "source_type": "finnhub_news",
                "source_url": "https://finnhub.io/n/nvda",
                "fetched_at": "2026-06-08T11:30:00+00:00",
                "summary": "news",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://sec.gov/edgar/nvda",
                "fetched_at": "2026-06-08T11:30:00+00:00",
                "summary": "10-Q",
            },
        ],
        "alternatives_considered": [
            {"description": "AMD", "why_rejected": "lower"},
        ],
    }


async def _seed_user_and_strategy(session: Any) -> StrategyRow:
    """Insert a User row, then a Strategy row. Returns the Strategy row."""
    session.add(
        User(
            user_id="test-user",
            created_at=datetime.now(UTC).isoformat(),
            kill_active=False,
        )
    )
    await session.flush()
    strategy_row = StrategyRow(
        strategy_id="strat-stamp-test",
        user_id="test-user",
        strategy_name="stamp-test",
        version=1,
        payload_json="{}",
        created_at=datetime.now(UTC).isoformat(),
    )
    session.add(strategy_row)
    await session.flush()
    return strategy_row


@pytest.mark.asyncio
async def test_paper_strategy_stamps_paper(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paper strategy → account_mode='PAPER' regardless of metadata."""
    # Disable wash-sale lookup (its session indirection isn't needed here)
    async def _no_flag(**_k: Any) -> None:
        return None

    monkeypatch.setattr(proposal_writer, "flag_wash_sale", _no_flag)

    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)

    strategy = _make_strategy(mode="paper")
    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id="strat-stamp-test",
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_trade_payload(),
        )
    assert isinstance(tp, TradeProposal)
    assert tp.account_mode == "PAPER"


@pytest.mark.asyncio
async def test_live_strategy_without_metadata_stamps_paper_defensively(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live strategy + missing StrategyMetadata row → defensive 'PAPER'."""
    async def _no_flag(**_k: Any) -> None:
        return None

    monkeypatch.setattr(proposal_writer, "flag_wash_sale", _no_flag)

    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)

    strategy = _make_strategy(mode="live")
    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id="strat-stamp-test",
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_trade_payload(),
        )
    assert tp.account_mode == "PAPER"


@pytest.mark.asyncio
async def test_live_strategy_with_eligibility_false_stamps_paper_defensively(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live strategy + live_mode_eligible=False → defensive 'PAPER'."""
    async def _no_flag(**_k: Any) -> None:
        return None

    monkeypatch.setattr(proposal_writer, "flag_wash_sale", _no_flag)

    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)
        session.add(
            StrategyMetadata(
                user_id="test-user",
                strategy_name="stamp-test",
                live_mode_eligible=False,
                live_promoted_at=None,
                first_live_trade_confirmed_at=None,
            )
        )

    strategy = _make_strategy(mode="live")
    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id="strat-stamp-test",
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_trade_payload(),
        )
    assert tp.account_mode == "PAPER"


@pytest.mark.asyncio
async def test_live_eligible_strategy_stamps_live(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live strategy + live_mode_eligible=True → 'LIVE'."""
    async def _no_flag(**_k: Any) -> None:
        return None

    monkeypatch.setattr(proposal_writer, "flag_wash_sale", _no_flag)

    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)
        session.add(
            StrategyMetadata(
                user_id="test-user",
                strategy_name="stamp-test",
                live_mode_eligible=True,
                live_promoted_at=datetime.now(UTC).isoformat(),
                first_live_trade_confirmed_at=None,
            )
        )

    strategy = _make_strategy(mode="live")
    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id="strat-stamp-test",
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_trade_payload(),
        )
    assert tp.account_mode == "LIVE"


@pytest.mark.asyncio
async def test_toctou_defense_promote_after_stamp_keeps_paper(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU: stamping account_mode=PAPER then promoting to live keeps stamp.

    Models the exact race the BLOCKER #5 closure prevents:
      T0: Researcher run on a paper-mode strategy → writer stamps 'PAPER'
      T1: operator promotes strategy to live + flips strategy.mode='live'
      T2: operator clicks Approve in Slack → handler reads
          tp.account_mode == 'PAPER' (NOT 'LIVE') and dispatches to the
          paper broker, not live.

    The assertion is that the persisted Proposal row's payload_json
    contains account_mode='PAPER' even after a promote_strategy_to_live.
    """
    from sqlalchemy import select

    from gekko.strategy.promotion import promote_strategy_to_live
    from gekko.strategy import promotion as promotion_mod

    async def _no_flag(**_k: Any) -> None:
        return None

    monkeypatch.setattr(proposal_writer, "flag_wash_sale", _no_flag)

    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        await _seed_user_and_strategy(session)

    # Wire promotion module's session factory to the test SF.
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # T0: stamp the proposal under a paper strategy. account_mode == 'PAPER'.
    strategy = _make_strategy(mode="paper")
    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id="strat-stamp-test",
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_trade_payload(),
        )
    assert tp.account_mode == "PAPER"

    # T1: operator promotes strategy + flips mode. The Strategy in-memory
    # state changes, but the PERSISTED Proposal row's payload_json is
    # frozen at T0.
    await promote_strategy_to_live(
        user_id="test-user", strategy_name="stamp-test"
    )

    # T2: re-read the proposal row from disk. Stamped account_mode is
    # STILL 'PAPER' (TOCTOU window closed).
    async with sf() as session:
        proposal_row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == decision_id
                )
            )
        ).scalar_one()
    assert proposal_row.account_mode == "PAPER"
    persisted_tp = TradeProposal.model_validate_json(
        proposal_row.payload_json
    )
    assert persisted_tp.account_mode == "PAPER"
