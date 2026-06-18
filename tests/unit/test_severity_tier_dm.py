"""Tests for severity-tier emoji prefix on DMs (REPT-01) — Plan 03-06 Task 3.

Tests:
1. MarketClosed branch DM contains ⚠️ prefix.
2. BrokerOrderError branch DM contains ❌ prefix.
3. kill_switch summary DM contains 🚫 prefix.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from gekko.brokers.base import OrderResult
from gekko.core.errors import BrokerOrderError
from gekko.core.types import OrderSide, OrderType
from gekko.db.models import Base, Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet


_USER_ID = "test-severity-user"
_STRATEGY_ID = "strat-severity-001"

_TEST_PASSPHRASE = "test-severity-passphrase"  # nosec: test-only


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_trade_proposal(
    *,
    user_id: str = _USER_ID,
    decision_id: str | None = None,
    account_mode: str = "PAPER",
) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="sev-test-strategy",
        decision_id=decision_id or uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=Decimal("1000.00"),
        order_type="limit",
        limit_price=Decimal("200.00"),
        rationale="Test rationale for severity tier tests.",
        confidence=Decimal("0.80"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-17T15:00:00+00:00",
                quote_text="NVDA quote",
                summary="Bullish signal",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/NVDA",
                fetched_at="2026-06-17T15:00:00+00:00",
                quote_text="NVDA news",
                summary="Positive news",
            ),
            EvidenceSnippet(
                source_type="web_fetch",
                source_url="https://example.com/nvda",
                fetched_at="2026-06-17T15:00:00+00:00",
                quote_text="Web content",
                summary="Neutral",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="AMD: Alternative chip maker",
                why_rejected="Lower confidence in short-term",
            )
        ],
        account_mode=account_mode,
        client_order_id="a" * 32,
    )


async def _seed_db(session: Any, proposal: TradeProposal, *, status: str = "APPROVED") -> None:
    """Seed user + strategy + proposal rows for executor tests."""
    from datetime import UTC, datetime

    now_iso = datetime.now(UTC).isoformat()
    session.add(User(user_id=_USER_ID, created_at=now_iso, agreement_acknowledged_at=now_iso))
    await session.flush()

    from gekko.schemas.strategy import HardCaps, Strategy as StrategySchema

    strat = StrategySchema(
        strategy_id=_STRATEGY_ID,
        user_id=_USER_ID,
        name="sev-test-strategy",
        version=1,
        thesis="Test",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.20"),
            max_daily_loss_usd=Decimal("999999"),
            max_trades_per_day=999,
            max_sector_exposure_pct=Decimal("1"),
        ),
        created_at=now_iso,
    )
    session.add(
        StrategyRow(
            strategy_id=_STRATEGY_ID,
            user_id=_USER_ID,
            strategy_name="sev-test-strategy",
            version=1,
            payload_json=strat.model_dump_json(),
            created_at=now_iso,
        )
    )
    await session.flush()

    session.add(
        ProposalRow(
            proposal_id=proposal.decision_id,
            user_id=_USER_ID,
            strategy_id=_STRATEGY_ID,
            status=status,
            payload_json=proposal.model_dump_json(),
            client_order_id=proposal.client_order_id,
            broker_order_id=None,
            created_at=now_iso,
            updated_at=now_iso,
            account_mode="PAPER",
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# (1) MarketClosed DM contains ⚠️ prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warning_emoji_prefix(tmp_path: Any) -> None:
    """MarketClosed branch emits DM with ⚠️ prefix."""
    from gekko.db.engine import get_async_engine
    from gekko.execution.executor import execute_proposal

    engine = get_async_engine(tmp_path / "sev_market_closed.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = make_session_factory(engine)
    tp = _make_trade_proposal()

    async with sf() as session, session.begin():
        await _seed_db(session, tp, status="APPROVED")

    sent_texts: list[str] = []

    async def _capture_dm(user_id: str, text: str) -> None:
        sent_texts.append(text)

    with (
        patch("gekko.execution.executor._get_session_factory", return_value=(sf, None)),
        patch("gekko.execution.executor.is_market_open", return_value=False),
        patch("gekko.execution.executor._send_slack_dm", side_effect=_capture_dm),
    ):
        await execute_proposal(tp.decision_id, _USER_ID)

    assert any("⚠️" in t for t in sent_texts), (
        f"Expected ⚠️ in market-closed DM, got: {sent_texts}"
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# (2) BrokerOrderError DM contains ❌ prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_emoji_prefix(tmp_path: Any) -> None:
    """BrokerOrderError branch emits DM with ❌ prefix."""
    from gekko.db.engine import get_async_engine
    from gekko.execution.executor import execute_proposal

    engine = get_async_engine(tmp_path / "sev_broker_error.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = make_session_factory(engine)
    tp = _make_trade_proposal()

    async with sf() as session, session.begin():
        await _seed_db(session, tp, status="APPROVED")

    sent_texts: list[str] = []

    async def _capture_dm(user_id: str, text: str) -> None:
        sent_texts.append(text)

    mock_broker = MagicMock()
    mock_broker.place_order = AsyncMock(
        side_effect=BrokerOrderError("Order placement failed: insufficient funds")
    )

    with (
        patch("gekko.execution.executor._get_session_factory", return_value=(sf, None)),
        patch("gekko.execution.executor.is_market_open", return_value=True),
        patch("gekko.execution.executor._build_broker", return_value=mock_broker),
        patch("gekko.execution.executor._send_slack_dm", side_effect=_capture_dm),
    ):
        await execute_proposal(tp.decision_id, _USER_ID)

    assert any("❌" in t for t in sent_texts), (
        f"Expected ❌ in broker-error DM, got: {sent_texts}"
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# (3) Kill switch summary DM contains 🚫 prefix
# ---------------------------------------------------------------------------


def test_kill_state_emoji_prefix() -> None:
    """Kill switch DM summary contains 🚫 prefix."""
    # Read the source bytes of kill_switch.py and verify 🚫 is present in the DM copy.
    # This is the static assertion — the dynamic DM content test is in test_kill_switch.py.
    import importlib.util
    from pathlib import Path

    ks_path = Path("src/gekko/execution/kill_switch.py")
    content_bytes = ks_path.read_bytes()

    # 🚫 encoded as UTF-8 is 0xF0 0x9F 0x9A 0xAB
    no_entry_utf8 = "🚫".encode("utf-8")
    assert no_entry_utf8 in content_bytes, (
        "Expected 🚫 emoji prefix in kill_switch.py DM text "
        "(severity-tier glyph map: kill-state changes → 🚫)"
    )
