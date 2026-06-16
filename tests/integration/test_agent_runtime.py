"""Integration tests for ``gekko.agent.runtime`` — Plan 01-07 Task 6.

Five tests covering the orchestrator wiring end-to-end with the Claude
Agent SDK mocked via the ``fake_sdk_query`` conftest fixture (the
``claude`` CLI binary is NOT required to run these tests — see
docs/sdk-shape.md delta #8).

1. ``trigger_strategy_run`` happy path → propose_trade → ProposalWriter
   inserts a Proposal row and 2 audit events (decision + proposal).
2. ``trigger_strategy_run`` no-action path → propose_no_action → 2
   audit events, NO proposal row.
3. Hallucinated ticker → ProposalRejected raised (caught at orchestrator
   layer); error event appended; no proposal row.
4. Active Guidance row is injected into the Researcher prompt.
5. ``compile_strategy_from_chat`` returns a validated Strategy with
   user_id/strategy_id/version/created_at filled by the runtime.

Every test marker: ``@pytest.mark.integration`` so the suite can be
filtered to either "fast unit" or "integration" runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.core.errors import ProposalRejected
from gekko.db.models import Event, Proposal as ProposalRow, User
from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import Guidance as GuidanceRow
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(
    user_id: str,
    *,
    name: str = "ai-infra-bull",
    watchlist: list[str] | None = None,
) -> Strategy:
    return Strategy(
        strategy_id="strat-" + uuid4().hex,
        user_id=user_id,
        name=name,
        version=1,
        thesis="Bullish on AI infrastructure providers.",
        watchlist=watchlist or ["NVDA", "AMD"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )


async def _seed(engine: Any, strategy: Strategy) -> None:
    Session = make_session_factory(engine)
    async with Session() as session, session.begin():
        session.add(
            User(
                user_id=strategy.user_id,
                created_at=datetime.now(UTC).isoformat(),
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
    """Build the <RESEARCH_BRIEF> text block the Researcher mock will emit."""
    payload = {
        "strategy_name": strategy.name,
        "user_id": user_id,
        "run_id": run_id,
        "generated_at": "2026-06-09T14:00:00+00:00",
        "tickers_examined": [
            {
                "ticker": "NVDA",
                "last_price": "180.40",
                "bid": "180.30",
                "ask": "180.50",
                "quote_ts": "2026-06-09T14:00:00+00:00",
            }
        ],
        "catalysts_observed": ["Earnings beat last week"],
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/a",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Earnings beat headline",
            }
        ],
        "research_budget_used": {"calls": 3, "tokens": 600, "seconds": 4.2},
        "notes": "Brief is intentionally minimal for the fake SDK.",
    }
    return f"<RESEARCH_BRIEF>\n{json.dumps(payload)}\n</RESEARCH_BRIEF>"


def _trade_tool_payload(ticker: str = "NVDA") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "side": "buy",
        "qty": "10",
        # Plan 02-01 Task 3 (D-27): LLM-authored dollar intent.
        "target_notional_usd": "1800.00",
        "order_type": "limit",
        "limit_price": "180.00",
        "rationale": "Brief signals a clean catalyst aligning with thesis.",
        "confidence": "0.7",
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/a",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Earnings beat headline",
            },
            {
                "source_type": "alpaca_quote",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Quote @ 180.40",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/x",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Recent 10-Q strong revenue",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Buy AMD instead",
                "why_rejected": "Already overweight.",
            }
        ],
    }


def _no_action_tool_payload() -> dict[str, Any]:
    return {
        "rationale": "Evidence thin; thesis not met today.",
        "factors_considered": [
            "No fresh catalyst",
            "Brief shows only one evidence item",
        ],
        "confidence": "0.6",
    }


# ---------------------------------------------------------------------------
# Test 1: propose_trade path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_strategy_run_propose_trade_path(
    temp_sqlcipher_db: Any,
    fake_sdk_query: Any,
) -> None:
    """Test 1: propose_trade path inserts row + 2 audit events."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "alice"
    strategy = _make_strategy(user_id)
    await _seed(temp_sqlcipher_db, strategy)
    session_factory = make_session_factory(temp_sqlcipher_db)

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

    # Note: run_id is generated inside trigger_strategy_run; the brief
    # we hand back uses a placeholder. ResearchBrief's run_id field is
    # informational — the writer uses the orchestrator's run_id for the
    # decision event's research_brief_run_id.
    result = await trigger_strategy_run(
        user_id=user_id,
        strategy_name=strategy.name,
        source="cli",
        session_factory=session_factory,
        broker=None,
    )

    assert result["outcome"] == "propose_trade"
    assert result["source"] == "cli"
    assert result["proposal"]["ticker"] == "NVDA"

    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session:
        rows = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        events = (
            await session.execute(
                select(Event).where(Event.user_id == user_id)
            )
        ).scalars().all()
        types = sorted({e.event_type for e in events})
        assert "decision" in types
        assert "proposal" in types

    # Two SDK calls expected (Researcher + Decision).
    keys = [c["key"] for c in fake_sdk_query.calls]
    assert keys == ["researcher", "decision"]
    # Verify D-11 invariant: decision call used the exact two-tool list.
    decision_call = fake_sdk_query.calls[1]
    assert decision_call["allowed_tools"] == [
        "mcp__gekko__propose_trade",
        "mcp__gekko__propose_no_action",
    ]


# ---------------------------------------------------------------------------
# Test 2: no_action path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_strategy_run_no_action_path(
    temp_sqlcipher_db: Any,
    fake_sdk_query: Any,
) -> None:
    """Test 2: propose_no_action — events present, no Proposal row."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "bob"
    strategy = _make_strategy(user_id)
    await _seed(temp_sqlcipher_db, strategy)
    session_factory = make_session_factory(temp_sqlcipher_db)

    fake_sdk_query.set_responses(
        researcher=[
            fake_sdk_query.make_text_message(
                _research_brief_text(strategy, user_id, "placeholder")
            )
        ],
        decision=[
            fake_sdk_query.make_tool_use_message(
                "mcp__gekko__propose_no_action", _no_action_tool_payload()
            )
        ],
    )

    result = await trigger_strategy_run(
        user_id=user_id,
        strategy_name=strategy.name,
        source="schedule",
        session_factory=session_factory,
    )

    assert result["outcome"] == "propose_no_action"

    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session:
        rows = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.user_id == user_id)
            )
        ).scalars().all()
        assert rows == []  # no row for no_action

        decision_event = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id, Event.event_type == "decision"
                )
            )
        ).scalar_one()
        parsed = json.loads(decision_event.payload_json)
        assert parsed["payload"]["decision_outcome"] == "no_action"

        proposal_event = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id, Event.event_type == "proposal"
                )
            )
        ).scalar_one()
        full = json.loads(proposal_event.payload_json)["payload"]
        assert "factors_considered" in full
        assert len(full["factors_considered"]) == 2


# ---------------------------------------------------------------------------
# Test 3: hallucinated ticker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_strategy_run_hallucinated_ticker(
    temp_sqlcipher_db: Any,
    fake_sdk_query: Any,
) -> None:
    """Test 3: ticker not in watchlist -> ProposalRejected; error event; no row."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "carol"
    strategy = _make_strategy(user_id, watchlist=["NVDA", "AMD"])
    await _seed(temp_sqlcipher_db, strategy)
    session_factory = make_session_factory(temp_sqlcipher_db)

    fake_sdk_query.set_responses(
        researcher=[
            fake_sdk_query.make_text_message(
                _research_brief_text(strategy, user_id, "placeholder")
            )
        ],
        decision=[
            fake_sdk_query.make_tool_use_message(
                "mcp__gekko__propose_trade",
                _trade_tool_payload(ticker="MEMECOIN"),
            )
        ],
    )

    with pytest.raises(ProposalRejected, match="MEMECOIN"):
        await trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy.name,
            source="slack",
            session_factory=session_factory,
        )

    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session:
        rows = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.user_id == user_id)
            )
        ).scalars().all()
        assert rows == []
        error_events = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id, Event.event_type == "error"
                )
            )
        ).scalars().all()
        assert len(error_events) == 1


# ---------------------------------------------------------------------------
# Test 4: active Guidance is injected into the Researcher prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_strategy_run_active_guidance_injected(
    temp_sqlcipher_db: Any,
    fake_sdk_query: Any,
) -> None:
    """Test 4: active Guidance text appears in the Researcher system_prompt."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "dave"
    strategy = _make_strategy(user_id)
    await _seed(temp_sqlcipher_db, strategy)

    # Insert an active Guidance row for this strategy.
    Session = make_session_factory(temp_sqlcipher_db)
    async with Session() as session, session.begin():
        session.add(
            GuidanceRow(
                guidance_id="g-1",
                user_id=user_id,
                strategy_id=strategy.strategy_id,
                text="focus on energy this week",
                scope="strategy",
                created_at=datetime.now(UTC).isoformat(),
                expires_at=None,
            )
        )

    fake_sdk_query.set_responses(
        researcher=[
            fake_sdk_query.make_text_message(
                _research_brief_text(strategy, user_id, "placeholder")
            )
        ],
        decision=[
            fake_sdk_query.make_tool_use_message(
                "mcp__gekko__propose_no_action", _no_action_tool_payload()
            )
        ],
    )

    await trigger_strategy_run(
        user_id=user_id,
        strategy_name=strategy.name,
        source="dashboard",
        session_factory=Session,
    )

    researcher_call = next(
        c for c in fake_sdk_query.calls if c["key"] == "researcher"
    )
    assert "focus on energy this week" in researcher_call["system_prompt"]


# ---------------------------------------------------------------------------
# Test 5: compile_strategy_from_chat returns a valid Strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_strategy_from_chat_returns_strategy(
    fake_sdk_query: Any,
) -> None:
    """Test 5: compile_strategy_from_chat parses the <STRATEGY> block."""
    from gekko.agent.runtime import compile_strategy_from_chat

    # The strategy JSON the LLM "emits" inside the <STRATEGY> block. Note
    # runtime-supplied fields (strategy_id, user_id, version, created_at,
    # created_by_chat) are filled by the function, NOT the LLM.
    fake_strategy_json = {
        "name": "energy-bull",
        "thesis": "Bullish on US energy independence plays for 2026.",
        "watchlist": ["XLE", "OXY", "CVX"],
        "hard_caps": {
            "max_position_pct": "0.05",
            "max_daily_loss_usd": "200",
            "max_trades_per_day": 3,
            "max_sector_exposure_pct": "0.25",
        },
        "schedule_time": None,
        "mode": "paper",
    }
    fake_sdk_query.set_responses(
        compiler=[
            fake_sdk_query.make_text_message(
                f"<STRATEGY>\n{json.dumps(fake_strategy_json)}\n</STRATEGY>"
            )
        ],
    )

    result = await compile_strategy_from_chat(
        user_id="alice",
        chat_transcript=(
            "I want to be bullish on US energy independence. Watch XLE, OXY, "
            "and CVX with conservative caps."
        ),
    )

    assert result.name == "energy-bull"
    assert result.user_id == "alice"
    assert result.version == 1
    assert result.watchlist == ["XLE", "OXY", "CVX"]
    assert result.created_by_chat is True
    assert result.created_at  # filled by runtime
    assert result.strategy_id.startswith("strat-")
