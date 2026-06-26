"""Wash-sale FLAG dict shape + ProposalWriter wiring — Plan 02-03 Task 3.

Locks two invariants:

1. **Dict shape (RESEARCH §5):** when ``flag_wash_sale`` returns a flag,
   the dict carries exactly these keys: ``would_be_wash_sale``,
   ``lookback_event_id``, ``lookback_date``, ``ticker``, ``lookback_qty``,
   ``lookback_side``, ``note``. Plans downstream (02-05 / 02-06 Slack
   card builder) read these keys.

2. **ProposalWriter wiring (D-28):** ``write_proposal`` stamps the flag
   onto ``TradeProposal.wash_sale_flag`` at proposal-build time. When no
   flag is generated, the field stays ``None``. When a flag is generated,
   the dict is preserved through the proposal-row payload_json and the
   audit-event proposal payload (D-15).

3. **OrderGuard invariant (EXEC-09 / D-29):** OrderGuard.place_order
   does NOT call ``flag_wash_sale``. The wash-sale is FLAG-only —
   surfaced at proposal build, never re-checked at execute time.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.agent.proposal_writer import write_proposal
from gekko.audit.log import append_event
from gekko.brokers.base import OrderRequest
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import Event, Proposal as ProposalRow, User
from gekko.db.session import make_session_factory
from gekko.execution.checks._wash_sale import flag_wash_sale
from gekko.schemas.proposal import TradeProposal
from gekko.schemas.strategy import HardCaps, Strategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_engine(temp_sqlcipher_db: Any) -> AsyncIterator[Any]:
    """SQLCipher engine + seeded ``users`` row."""
    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    yield temp_sqlcipher_db


def _patch_wash_sale_session_factory(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """Patch the wash-sale module's ``_get_session_factory``."""
    from gekko.execution.checks import _wash_sale as ws_mod

    sf = make_session_factory(engine)
    monkeypatch.setattr(
        ws_mod,
        "_get_session_factory",
        lambda user_id: (sf, None),
        raising=True,
    )


def _make_strategy(watchlist: list[str] | None = None) -> tuple[Strategy, str]:
    """Build a Pydantic Strategy + its DB strategy_id."""
    strategy_db_id = "strat-" + uuid4().hex
    s = Strategy(
        strategy_id=strategy_db_id,
        user_id="test-user",
        name="ai-infra-bull",
        version=1,
        thesis="Bullish on AI infrastructure providers.",
        watchlist=watchlist or ["AAPL", "NVDA", "MSFT"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )
    return s, strategy_db_id


def _llm_trade_payload(ticker: str = "AAPL") -> dict[str, Any]:
    """A minimal LLM-emitted TradeProposal payload."""
    return {
        "ticker": ticker,
        "side": "buy",
        "qty": "10",
        "target_notional_usd": "1000.00",
        "order_type": "limit",
        "limit_price": "100.00",
        "confidence": "0.75",
        "rationale": "Test rationale for wash-sale flag test.",
        "evidence": [
            {
                "source_type": "alpaca_quote",
                "source_url": "https://api.example.com/data1",
                "fetched_at": datetime.now(UTC).isoformat(),
                "summary": "Evidence one summary",
                "quote_text": "Test quote 1",
            },
            {
                "source_type": "alpaca_quote",
                "source_url": "https://api.example.com/data2",
                "fetched_at": datetime.now(UTC).isoformat(),
                "summary": "Evidence two summary",
                "quote_text": "Test quote 2",
            },
            {
                "source_type": "alpaca_quote",
                "source_url": "https://api.example.com/data3",
                "fetched_at": datetime.now(UTC).isoformat(),
                "summary": "Evidence three summary",
                "quote_text": "Test quote 3",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Wait for pullback",
                "why_rejected": "Trend remains intact",
            }
        ],
    }


async def _seed_strategy_row(engine: Any, strategy_db_id: str) -> None:
    """Seed a strategies row so the foreign key holds."""
    from gekko.db.models import Strategy as StrategyRow

    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        session.add(
            StrategyRow(
                strategy_id=strategy_db_id,
                user_id="test-user",
                strategy_name="ai-infra-bull",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )


async def _seed_fill(
    engine: Any, *, ticker: str, days_ago: int, side: str = "sell"
) -> int:
    """Seed a fill event ``days_ago`` calendar days in the past."""
    sf = make_session_factory(engine)
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    async with sf() as session, session.begin():
        row = await append_event(
            session,
            user_id="test-user",
            strategy_id=None,
            event_type="fill",
            payload={
                "ticker": ticker,
                "side": side,
                "filled_qty": "10",
            },
            ts=ts,
        )
        return row.id


# ---------------------------------------------------------------------------
# 1. Dict shape lock (RESEARCH §5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_flag_dict_shape(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag dict carries exactly the locked keys per RESEARCH §5."""
    _patch_wash_sale_session_factory(monkeypatch, seeded_engine)
    event_id = await _seed_fill(seeded_engine, ticker="AAPL", days_ago=10)

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )
    flag = await flag_wash_sale(req=req, user_id="test-user")
    assert flag is not None

    expected_keys = {
        "would_be_wash_sale",
        "lookback_event_id",
        "lookback_date",
        "ticker",
        "lookback_qty",
        "lookback_side",
        "note",
    }
    assert set(flag.keys()) == expected_keys, (
        f"flag dict keys must match locked set; "
        f"missing={expected_keys - set(flag.keys())!r}, "
        f"extra={set(flag.keys()) - expected_keys!r}"
    )
    # Values are correctly populated.
    assert flag["would_be_wash_sale"] is True
    assert flag["lookback_event_id"] == event_id
    assert flag["ticker"] == "AAPL"
    assert flag["lookback_side"] == "sell"
    assert isinstance(flag["note"], str) and len(flag["note"]) > 0


# ---------------------------------------------------------------------------
# 2. ProposalWriter wiring (D-28)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_writer_stamps_wash_sale_flag(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ProposalWriter calls flag_wash_sale and stamps the result onto
    ``TradeProposal.wash_sale_flag`` at proposal-build time."""
    _patch_wash_sale_session_factory(monkeypatch, seeded_engine)

    strategy, strategy_db_id = _make_strategy()
    await _seed_strategy_row(seeded_engine, strategy_db_id)
    # Seed a prior AAPL fill 15 days ago.
    await _seed_fill(seeded_engine, ticker="AAPL", days_ago=15)

    sf = make_session_factory(seeded_engine)
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id="run-" + uuid4().hex,
            decision_id="dec-" + uuid4().hex,
            tool_outcome="propose_trade",
            payload=_llm_trade_payload("AAPL"),
        )

    assert isinstance(tp, TradeProposal)
    assert tp.wash_sale_flag is not None
    assert tp.wash_sale_flag["ticker"] == "AAPL"
    assert tp.wash_sale_flag["would_be_wash_sale"] is True


@pytest.mark.asyncio
async def test_proposal_writer_leaves_flag_none_when_no_match(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prior fill within 30 days -> flag stays None."""
    _patch_wash_sale_session_factory(monkeypatch, seeded_engine)

    strategy, strategy_db_id = _make_strategy()
    await _seed_strategy_row(seeded_engine, strategy_db_id)
    # Seed a fill 40 days ago (outside window) so the walk runs but
    # finds no match.
    await _seed_fill(seeded_engine, ticker="AAPL", days_ago=40)

    sf = make_session_factory(seeded_engine)
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id="run-" + uuid4().hex,
            decision_id="dec-" + uuid4().hex,
            tool_outcome="propose_trade",
            payload=_llm_trade_payload("AAPL"),
        )

    assert tp.wash_sale_flag is None


@pytest.mark.asyncio
async def test_proposal_writer_persists_flag_in_payload_json(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proposal row's payload_json carries the wash_sale_flag dict."""
    _patch_wash_sale_session_factory(monkeypatch, seeded_engine)

    strategy, strategy_db_id = _make_strategy()
    await _seed_strategy_row(seeded_engine, strategy_db_id)
    await _seed_fill(seeded_engine, ticker="AAPL", days_ago=5)

    decision_id = "dec-" + uuid4().hex
    sf = make_session_factory(seeded_engine)
    async with sf() as session, session.begin():
        await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id="run-" + uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_llm_trade_payload("AAPL"),
        )

    # Reload the row and inspect payload_json.
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()
    payload = json.loads(row.payload_json)
    assert "wash_sale_flag" in payload
    assert payload["wash_sale_flag"] is not None
    assert payload["wash_sale_flag"]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_proposal_writer_persists_flag_in_proposal_audit_event(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit ``proposal`` event payload carries the flag (D-15 — full
    structured rationale persisted)."""
    _patch_wash_sale_session_factory(monkeypatch, seeded_engine)

    strategy, strategy_db_id = _make_strategy()
    await _seed_strategy_row(seeded_engine, strategy_db_id)
    await _seed_fill(seeded_engine, ticker="AAPL", days_ago=5)

    decision_id = "dec-" + uuid4().hex
    sf = make_session_factory(seeded_engine)
    async with sf() as session, session.begin():
        await write_proposal(
            session,
            user_id="test-user",
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id="run-" + uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=_llm_trade_payload("AAPL"),
        )

    async with sf() as session:
        rows = (
            await session.execute(
                select(Event).where(
                    Event.user_id == "test-user",
                    Event.event_type == "proposal",
                )
            )
        ).scalars().all()
    assert len(rows) >= 1
    # The most-recent proposal event corresponds to our write.
    canonical = json.loads(rows[-1].payload_json)
    payload = canonical.get("payload", {})
    assert "wash_sale_flag" in payload
    assert payload["wash_sale_flag"] is not None
    assert payload["wash_sale_flag"]["ticker"] == "AAPL"


# ---------------------------------------------------------------------------
# 3. OrderGuard invariant (EXEC-09 / D-29) — does NOT call flag_wash_sale
# ---------------------------------------------------------------------------


def test_orderguard_does_not_import_flag_wash_sale() -> None:
    """OrderGuard's source MUST NOT import or call ``flag_wash_sale``.

    EXEC-09 / D-29 — wash-sale is FLAG-only, attached at ProposalWriter
    time. OrderGuard does NOT re-check at place_order time."""
    import gekko.execution.orderguard as og_mod

    src = Path(og_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Walk all Name + Attribute + Call nodes.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "flag_wash_sale", (
                "OrderGuard module must NOT reference flag_wash_sale "
                "(EXEC-09 — wash-sale is FLAG-only, attached at "
                "ProposalWriter time, never re-checked at place_order)"
            )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "flag_wash_sale", (
                    "OrderGuard module must NOT import flag_wash_sale"
                )


@pytest.mark.asyncio
async def test_orderguard_place_order_does_not_block_on_wash_sale(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal with ``wash_sale_flag`` set still flows through OrderGuard
    successfully — the flag is informational, not blocking."""
    from unittest.mock import AsyncMock, MagicMock

    from gekko.execution.orderguard import OrderGuard

    # Build a TradeProposal carrying a wash_sale_flag.
    strategy, _ = _make_strategy()
    tp_payload = _llm_trade_payload("AAPL")
    tp = TradeProposal(
        user_id="test-user",
        strategy_name=strategy.name,
        decision_id="dec-" + uuid4().hex,
        ticker="AAPL",
        side=OrderSide.BUY,
        qty=Decimal(tp_payload["qty"]),
        target_notional_usd=Decimal(tp_payload["target_notional_usd"]),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(tp_payload["limit_price"]),
        rationale=tp_payload["rationale"],
        confidence=Decimal(tp_payload["confidence"]),
        evidence=tp_payload["evidence"],
        alternatives_considered=tp_payload["alternatives_considered"],
        client_order_id="0" * 32,
        account_mode="PAPER",
        wash_sale_flag={
            "would_be_wash_sale": True,
            "lookback_event_id": 1,
            "lookback_date": "2026-06-01T12:00:00+00:00",
            "ticker": "AAPL",
            "lookback_qty": "10",
            "lookback_side": "sell",
            "note": "test wash sale note",
        },
    )

    # Stub the wrapped broker (all GETs return safe values; place_order
    # returns an OrderResult).
    from gekko.brokers.base import OrderResult

    wrapped = MagicMock()
    wrapped.is_paper = True
    wrapped.name = "alpaca"
    wrapped.supports_fractional = True
    wrapped.get_account = AsyncMock(
        return_value={
            "equity": "100000",
            "non_marginable_buying_power": "50000",
            "shorting_enabled": True,  # margin -> T+1 exempt
            "pattern_day_trader": False,
            "daytrade_count": "0",
            "portfolio_value": "100000",
        }
    )
    wrapped.get_positions = AsyncMock(return_value=[])
    wrapped.get_quote = AsyncMock(return_value={"ask_price": "100"})
    wrapped.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="bid-1",
            client_order_id="0" * 32,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={},
        )
    )

    # Patch all DB-touching checks to no-ops so we don't need a real
    # SQLCipher passphrase. The only invariant we're proving here is that
    # OrderGuard does NOT itself re-check wash-sale; the wash-sale flag
    # rides on the proposal as informational metadata.
    from gekko.execution import orderguard as og_mod

    async def _noop_kill(_user_id: str) -> None:
        return None

    async def _noop_market(_req: Any) -> None:
        return None

    async def _noop_hard_caps(*, req: Any, strategy: Any, broker: Any, user_id: str) -> None:
        return None

    async def _noop_pdt(*, req: Any, account: Any, user_id: str) -> None:
        return None

    async def _noop_universe(req: Any, *, strategy: Any) -> None:
        return None

    async def _noop_portfolio_caps(
        *, req: Any, strategy: Any, broker: Any, user_id: str
    ) -> None:
        return None

    async def _noop_capital_ceiling(
        *, req: Any, strategy: Any, broker: Any, user_id: str
    ) -> None:
        return None

    monkeypatch.setattr(og_mod, "check_kill_switch", _noop_kill)
    monkeypatch.setattr(og_mod, "check_market_hours", _noop_market)
    monkeypatch.setattr(og_mod, "check_hard_caps", _noop_hard_caps)
    monkeypatch.setattr(og_mod, "check_pdt", _noop_pdt)
    monkeypatch.setattr(og_mod, "check_universe", _noop_universe)
    # Phase 5: portfolio caps + capital ceiling also touch the vault-backed DB.
    # No-op them too — this test only proves OrderGuard does not RE-CHECK
    # wash-sale, not the Phase-5 cap behavior (covered by their own tests).
    monkeypatch.setattr(og_mod, "check_portfolio_caps", _noop_portfolio_caps)
    monkeypatch.setattr(og_mod, "check_capital_ceiling", _noop_capital_ceiling)

    guard = OrderGuard(
        wrapped,
        strategy=strategy,
        account_mode="PAPER",
        user_id="test-user",
        proposal=tp,
    )

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )

    # Must not raise — wash_sale_flag is informational.
    result = await guard.place_order(req)
    assert result.broker_order_id == "bid-1"
    # Verify the wrapped broker's place_order was called (proposal flowed
    # through OrderGuard).
    wrapped.place_order.assert_awaited_once()
