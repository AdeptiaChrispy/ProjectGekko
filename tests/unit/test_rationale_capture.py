"""REPT-04 rationale-capture verification — Plan 01-07 Task 5.

Per D-15: every persisted "proposal" event in the audit log MUST contain
the full structured rationale (evidence + alternatives + confidence) so
the v2 retrospective dashboard can reconstruct the Decision agent's
reasoning without replaying the LLM call.

This file is a targeted contract test: write one ``propose_trade`` with
4 evidence snippets + 2 alternatives + confidence=Decimal("0.75"), then
query the "proposal" event row and assert all four evidence summaries,
both alternative descriptions, and the confidence value round-trip
intact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.db.models import Event, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy


@pytest.mark.asyncio
async def test_proposal_event_contains_full_rationale(
    temp_sqlcipher_db: Any,
) -> None:
    """REPT-04: a proposal event's payload carries 4 evidence + 2 alternatives + confidence."""
    from gekko.agent.proposal_writer import write_proposal

    user_id = "rept04-user"
    strategy = Strategy(
        strategy_id="strat-rept04",
        user_id=user_id,
        name="rept04-strategy",
        version=1,
        thesis="Test the rationale-capture contract.",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )

    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session, session.begin():
        session.add(
            User(user_id=user_id, created_at=datetime.now(UTC).isoformat())
        )
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=user_id,
                strategy_name=strategy.name,
                version=strategy.version,
                payload_json=strategy.model_dump_json(),
                created_at=strategy.created_at,
            )
        )

    decision_id = uuid4().hex
    run_id = uuid4().hex

    # 4 evidence snippets (within the [3,5] D-12 bound) and 2 alternatives.
    payload: dict[str, Any] = {
        "ticker": "NVDA",
        "side": "buy",
        "qty": "10",
        "order_type": "limit",
        "limit_price": "180.00",
        "rationale": "Comprehensive thesis: earnings + sector + 10-Q + price-action.",
        "confidence": "0.75",
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/a",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Evidence-A: earnings beat headline",
            },
            {
                "source_type": "finnhub_news",
                "source_url": "https://wsj.com/b",
                "fetched_at": "2026-06-09T14:00:01+00:00",
                "summary": "Evidence-B: analyst upgrade",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1/c.htm",
                "fetched_at": "2026-06-09T14:00:02+00:00",
                "summary": "Evidence-C: 10-Q revenue growth",
            },
            {
                "source_type": "alpaca_quote",
                "fetched_at": "2026-06-09T14:00:03+00:00",
                "summary": "Evidence-D: quote @ 180.40",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Alt-A: buy AMD instead",
                "why_rejected": "Already overweight AMD",
            },
            {
                "description": "Alt-B: wait for 175 entry",
                "why_rejected": "Momentum thesis prefers entry near recent breakout",
            },
        ],
    }

    async with Session() as session, session.begin():
        await write_proposal(
            session,
            user_id=user_id,
            strategy=strategy,
            strategy_db_id=strategy.strategy_id,
            run_id=run_id,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=payload,
            prompt_model="sonnet",
        )

    async with Session() as session:
        proposal_event = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id, Event.event_type == "proposal"
                )
            )
        ).scalar_one()
        parsed = json.loads(proposal_event.payload_json)
        full = parsed["payload"]

        # All 4 evidence summaries present.
        summaries = {e["summary"] for e in full["evidence"]}
        assert {"Evidence-A: earnings beat headline", "Evidence-B: analyst upgrade",
                "Evidence-C: 10-Q revenue growth", "Evidence-D: quote @ 180.40"} <= summaries

        # Both alternatives present.
        alt_descs = {a["description"] for a in full["alternatives_considered"]}
        assert {"Alt-A: buy AMD instead", "Alt-B: wait for 175 entry"} <= alt_descs

        # Confidence preserved (normalize_decimals collapses trailing zeros).
        # "0.75".normalize() -> "0.75" — unchanged. We just need round-tripping.
        assert full["confidence"] == "0.75"


@pytest.mark.asyncio
async def test_no_action_event_contains_factors_considered(
    temp_sqlcipher_db: Any,
) -> None:
    """REPT-04 (no-action variant): factors_considered + confidence round-trip."""
    from gekko.agent.proposal_writer import write_proposal

    user_id = "rept04-na-user"
    strategy = Strategy(
        strategy_id="strat-rept04-na",
        user_id=user_id,
        name="rept04-na-strategy",
        version=1,
        thesis="Test the no-action rationale-capture contract.",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )

    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session, session.begin():
        session.add(
            User(user_id=user_id, created_at=datetime.now(UTC).isoformat())
        )
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=user_id,
                strategy_name=strategy.name,
                version=strategy.version,
                payload_json=strategy.model_dump_json(),
                created_at=strategy.created_at,
            )
        )

    decision_id = uuid4().hex
    run_id = uuid4().hex
    payload: dict[str, Any] = {
        "rationale": "Catalysts insufficient + market regime uncertain.",
        "factors_considered": [
            "Factor-A: No fresh news in 7 days",
            "Factor-B: Price elevated vs 50-DMA",
            "Factor-C: VIX > 25 — risk-off regime",
        ],
        "confidence": "0.6",
    }

    async with Session() as session, session.begin():
        await write_proposal(
            session,
            user_id=user_id,
            strategy=strategy,
            strategy_db_id=strategy.strategy_id,
            run_id=run_id,
            decision_id=decision_id,
            tool_outcome="propose_no_action",
            payload=payload,
        )

    async with Session() as session:
        proposal_event = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id, Event.event_type == "proposal"
                )
            )
        ).scalar_one()
        parsed = json.loads(proposal_event.payload_json)
        full = parsed["payload"]
        assert len(full["factors_considered"]) == 3
        assert full["confidence"] == "0.6"
