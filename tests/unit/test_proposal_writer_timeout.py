"""ProposalWriter expires_at stamping tests — Plan 03-01 Task 3.

Exercises three cases per D-51:
(a) strategy with proposal_timeout_minutes=None -> expires_at = now + 30min
(b) strategy with proposal_timeout_minutes=15 -> expires_at = now + 15min
(c) freezegun-pinned now so the expected ISO string matches
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from gekko.agent.proposal_writer import PROPOSAL_TIMEOUT_DEFAULT_MIN, write_proposal
from gekko.db.models import Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FREEZE_TIME = "2026-06-17T12:00:00+00:00"


def _make_strategy(
    *,
    user_id: str = "test-user",
    proposal_timeout_minutes: int | None = None,
) -> tuple[Strategy, str]:
    strategy_db_id = "strat-" + uuid4().hex
    s = Strategy(
        strategy_id=strategy_db_id,
        user_id=user_id,
        name="ai-infra-bull",
        version=1,
        thesis="Bullish on AI infrastructure providers.",
        watchlist=["NVDA", "AMD", "AVGO"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
        proposal_timeout_minutes=proposal_timeout_minutes,
    )
    return s, strategy_db_id


def _llm_trade_payload(ticker: str = "NVDA") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "side": "buy",
        "qty": "10",
        "target_notional_usd": "1800.00",
        "order_type": "limit",
        "limit_price": "180.00",
        "rationale": "Earnings beat + analyst upgrade aligns with thesis.",
        "confidence": "0.75",
        "evidence": [
            {
                "source_type": "finnhub_news",
                "source_url": "https://reuters.com/x",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Strong earnings beat.",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/Archives/edgar/data/x/y.htm",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Recent 10-Q shows revenue growth.",
            },
            {
                "source_type": "alpaca_quote",
                "fetched_at": "2026-06-09T14:00:00+00:00",
                "summary": "Quote @ $180.40.",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Buy AMD instead",
                "why_rejected": "AMD already in position; over-concentration.",
            },
        ],
    }


async def _seed_user_and_strategy(
    session_factory: Any,
    strategy: Strategy,
    strategy_db_id: str,
    *,
    user_id: str = "test-user",
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_db_id,
                user_id=user_id,
                strategy_name=strategy.name,
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@freeze_time(_FREEZE_TIME)
async def test_default_timeout_thirty_minutes(temp_sqlcipher_db: Any) -> None:
    """strategy with proposal_timeout_minutes=None -> expires_at = now + 30min."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy, strategy_db_id = _make_strategy(proposal_timeout_minutes=None)
    await _seed_user_and_strategy(sf, strategy, strategy_db_id)

    decision_id = uuid4().hex
    async with sf() as session, session.begin():
        # Patch _get_session_factory / _get_passphrase shims used by proposal_writer
        # flag_wash_sale — it uses a different session; let it no-op via monkeypatch
        import unittest.mock

        with unittest.mock.patch(
            "gekko.agent.proposal_writer.flag_wash_sale",
            new_callable=unittest.mock.AsyncMock,
            return_value=None,
        ):
            await write_proposal(
                session,
                user_id="test-user",
                strategy=strategy,
                strategy_db_id=strategy_db_id,
                run_id=uuid4().hex,
                decision_id=decision_id,
                tool_outcome="propose_trade",
                payload=_llm_trade_payload(),
            )

    # Verify expires_at = frozen_now + 30 min
    expected_expires = (
        datetime.fromisoformat(_FREEZE_TIME).replace(tzinfo=UTC)
        + timedelta(minutes=PROPOSAL_TIMEOUT_DEFAULT_MIN)
    ).isoformat()

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()
        assert row.expires_at is not None
        assert row.expires_at == expected_expires


@pytest.mark.asyncio
@freeze_time(_FREEZE_TIME)
async def test_custom_timeout_fifteen_minutes(temp_sqlcipher_db: Any) -> None:
    """strategy with proposal_timeout_minutes=15 -> expires_at = now + 15min."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy, strategy_db_id = _make_strategy(proposal_timeout_minutes=15)
    await _seed_user_and_strategy(sf, strategy, strategy_db_id)

    decision_id = uuid4().hex
    import unittest.mock

    async with sf() as session, session.begin():
        with unittest.mock.patch(
            "gekko.agent.proposal_writer.flag_wash_sale",
            new_callable=unittest.mock.AsyncMock,
            return_value=None,
        ):
            await write_proposal(
                session,
                user_id="test-user",
                strategy=strategy,
                strategy_db_id=strategy_db_id,
                run_id=uuid4().hex,
                decision_id=decision_id,
                tool_outcome="propose_trade",
                payload=_llm_trade_payload(),
            )

    expected_expires = (
        datetime.fromisoformat(_FREEZE_TIME).replace(tzinfo=UTC)
        + timedelta(minutes=15)
    ).isoformat()

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()
        assert row.expires_at == expected_expires


@pytest.mark.asyncio
@freeze_time(_FREEZE_TIME)
async def test_expires_at_iso_matches_frozen_time(temp_sqlcipher_db: Any) -> None:
    """expires_at ISO string matches exactly with freezegun-pinned now."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy, strategy_db_id = _make_strategy(proposal_timeout_minutes=60)
    await _seed_user_and_strategy(sf, strategy, strategy_db_id)

    decision_id = uuid4().hex
    import unittest.mock

    async with sf() as session, session.begin():
        with unittest.mock.patch(
            "gekko.agent.proposal_writer.flag_wash_sale",
            new_callable=unittest.mock.AsyncMock,
            return_value=None,
        ):
            await write_proposal(
                session,
                user_id="test-user",
                strategy=strategy,
                strategy_db_id=strategy_db_id,
                run_id=uuid4().hex,
                decision_id=decision_id,
                tool_outcome="propose_trade",
                payload=_llm_trade_payload(),
            )

    frozen_now = datetime.fromisoformat(_FREEZE_TIME).replace(tzinfo=UTC)
    expected = (frozen_now + timedelta(minutes=60)).isoformat()

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
            )
        ).scalar_one()
        assert row.expires_at == expected
