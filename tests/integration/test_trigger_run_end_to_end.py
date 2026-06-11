"""Walking-skeleton end-to-end test — Plan 01-09 Task 4 (the wave gate).

Executes SKELETON.md §Demo Script as automated cassette-mode replay.
Proves the Phase 1 capability is alive end-to-end:

  1. User + Strategy seeded with REG-02 agreement acknowledged.
  2. ``trigger_strategy_run`` orchestrates Researcher -> Decision ->
     ProposalWriter via the SDK-mocked ``fake_sdk_query`` fixture (the
     real ``claude`` CLI is not required — Plan 01-07 docs/sdk-shape.md
     delta #8). Result: 2 audit events (decision, proposal) + 1 Proposal
     row in PENDING (D-11, D-15, D-20).
  3. :func:`build_proposal_card` renders the HITL-01 Block Kit card
     from the persisted TradeProposal — verified to contain the PAPER
     banner + the deterministic approve_proposal action_id.
  4. :func:`approve_proposal` transitions PENDING -> APPROVED and emits
     the approval audit event (HITL-04).
  5. :func:`execute_proposal` with a mocked :class:`AlpacaBroker` runs
     the market-hours guard (forced True), constructs an OrderRequest
     with the persisted deterministic client_order_id, "places" the
     order, and emits order_submitted (EXEC-02 / EXEC-07 / EXEC-10).
  6. :func:`on_fill_event` writes the fill audit event, transitions
     EXECUTING -> FILLED, and triggers the Slack DM confirmation
     (BROK-A-06 — wired as the FillCallback in Plan 01-09's lifespan).
  7. :func:`walk_chain` returns ``[]`` — the SHA-256 hash chain across
     all 5 events is intact (AUDT-01 + AUDT-02).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.approval.proposals import approve_proposal
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.brokers.base import OrderResult
from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory
from gekko.reporter.slack import build_proposal_card
from gekko.schemas.proposal import TradeProposal
from gekko.schemas.strategy import HardCaps, Strategy

pytestmark = pytest.mark.integration


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_strategy(user_id: str) -> Strategy:
    return Strategy(
        strategy_id="strat-" + uuid4().hex,
        user_id=user_id,
        name="ai-infra-bull",
        version=1,
        thesis="Bullish on AI infrastructure providers (Phase 1 demo).",
        watchlist=["NVDA", "AMD", "AVGO"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("200"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        mode="paper",
        schedule_time=None,
        created_at=_now_iso(),
        created_by_chat=False,
    )


async def _seed(engine: Any, strategy: Strategy) -> None:
    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        # REG-02: agreement_acknowledged_at populated.
        session.add(
            User(
                user_id=strategy.user_id,
                created_at=_now_iso(),
                agreement_acknowledged_at=_now_iso(),
            )
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=strategy.user_id,
                strategy_name=strategy.name,
                version=strategy.version,
                payload_json=strategy.model_dump_json(),
                created_at=strategy.created_at,
            )
        )


def _research_brief_text(strategy: Strategy, user_id: str, run_id: str) -> str:
    payload = {
        "strategy_name": strategy.name,
        "user_id": user_id,
        "run_id": run_id,
        "generated_at": "2026-06-11T10:00:00+00:00",
        "tickers_examined": [
            {
                "ticker": "NVDA",
                "last_price": "1234.56",
                "bid": "1234.40",
                "ask": "1234.72",
                "quote_ts": "2026-06-11T10:00:00+00:00",
            }
        ],
        "catalysts_observed": ["Earnings beat last week"],
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/x",
                "fetched_at": "2026-06-11T10:00:00+00:00",
                "summary": "Earnings beat headline",
            }
        ],
        "research_budget_used": {"calls": 3, "tokens": 600, "seconds": 4.2},
        "notes": "Cassette-mode replay for the walking-skeleton gate.",
    }
    return f"<RESEARCH_BRIEF>\n{json.dumps(payload)}\n</RESEARCH_BRIEF>"


def _trade_tool_payload() -> dict[str, Any]:
    return {
        "ticker": "NVDA",
        "side": "buy",
        "qty": "5",
        "order_type": "limit",
        "limit_price": "1234.56",
        "rationale": "Catalyst + research brief support a small position.",
        "confidence": "0.78",
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/x",
                "fetched_at": "2026-06-11T10:00:00+00:00",
                "summary": "Earnings beat headline",
            },
            {
                "source_type": "alpaca_quote",
                "source_url": "https://alpaca.markets/quotes/NVDA",
                "fetched_at": "2026-06-11T10:00:00+00:00",
                "summary": "Quote @ 1234.56",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/Archives/edgar/data/x/",
                "fetched_at": "2026-06-11T10:00:00+00:00",
                "summary": "Recent 10-Q strong revenue",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Buy AMD instead",
                "why_rejected": "Already overweight data-center exposure.",
            }
        ],
    }


@pytest.mark.asyncio
async def test_walking_skeleton_end_to_end(
    temp_sqlcipher_db: Any,
    fake_sdk_query: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """The Phase 1 wave gate — full SKELETON Demo Script flow."""
    from gekko.agent.runtime import trigger_strategy_run
    from gekko.execution import executor

    # ---- 1. Seed User (REG-02 ack) + Strategy. ---------------------------
    user_id = "alice"
    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    from gekko.config import get_settings

    get_settings.cache_clear()

    strategy = _make_strategy(user_id)
    await _seed(temp_sqlcipher_db, strategy)
    sf = make_session_factory(temp_sqlcipher_db)

    # ---- 2. trigger_strategy_run — fake_sdk_query supplies the brief +
    #         the propose_trade tool call.                                 -
    fake_sdk_query.set_responses(
        researcher=[
            fake_sdk_query.make_text_message(
                _research_brief_text(strategy, user_id, "placeholder-run-id")
            )
        ],
        decision=[
            fake_sdk_query.make_tool_use_message(
                "mcp__gekko__propose_trade", _trade_tool_payload()
            )
        ],
    )

    result = await trigger_strategy_run(
        user_id=user_id,
        strategy_name="ai-infra-bull",
        source="test",
        session_factory=sf,
    )
    assert result["outcome"] == "propose_trade"
    decision_id = result["decision_id"]

    # ---- 3. HITL-01 Block Kit card — built from the persisted proposal.
    async with sf() as session:
        proposal_row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()
        tp = TradeProposal.model_validate_json(proposal_row.payload_json)

    card = build_proposal_card(tp, account_mode="PAPER")
    serialized = json.dumps(card)
    assert "PAPER" in serialized
    assert "approve_proposal" in serialized

    # ---- 4. HITL-04 approve — PENDING -> APPROVED + approval event. -----
    async with sf() as session, session.begin():
        await approve_proposal(session, decision_id, actor="U_TEST")

    # ---- 5. Executor — mocked broker + mocked market_hours + mocked DMs.
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="ORDER-WAVE-GATE-001",
            client_order_id=tp.client_order_id,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": "ORDER-WAVE-GATE-001", "status": "accepted"},
        )
    )
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    sent_dms: list[str] = []

    async def _fake_dm(_uid: str, msg: str) -> None:
        sent_dms.append(msg)

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)

    await executor.execute_proposal(decision_id, user_id)

    # ---- 6. on_fill_event — EXECUTING -> FILLED + fill event + DM. ------
    fill_payload = {
        "client_order_id": tp.client_order_id,
        "broker_order_id": "ORDER-WAVE-GATE-001",
        "filled_qty": "5",
        "filled_avg_price": "1234.50",
        "ticker": "NVDA",
        "user_id": user_id,
        "event": "fill",
    }
    await executor.on_fill_event(fill_payload, user_id=user_id)

    # ---- 7. Walk the chain — assert 5 events in order + intact chain. ---
    async with sf() as session:
        events = (
            await session.execute(
                select(Event)
                .where(Event.user_id == user_id)
                .order_by(Event.id.asc())
            )
        ).scalars().all()
        breaks = await walk_chain(session, user_id)

        # Confirm the proposal row ended in FILLED.
        proposal_row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()

    event_types = [e.event_type for e in events]
    assert event_types == [
        "decision",
        "proposal",
        "approval",
        "order_submitted",
        "fill",
    ], f"unexpected audit chain: {event_types}"

    assert breaks == [], (
        f"SHA-256 chain broken at row(s): {breaks}; "
        f"events: {event_types}"
    )

    assert proposal_row.status == "FILLED"
    assert proposal_row.broker_order_id == "ORDER-WAVE-GATE-001"

    # Slack DM confirmation arrived (the fill-stream callback's DM).
    assert any("filled" in m.lower() for m in sent_dms), sent_dms
