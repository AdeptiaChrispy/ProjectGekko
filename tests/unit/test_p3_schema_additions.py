"""Phase 3 schema additions test — Plan 03-01 Task 3.

Exercises the ORM + Pydantic additions from Task 2:
- Insert a SlackActionDedup row; query it back
- User.quiet_hours_* + timezone columns queryable
- Proposal.expires_at + slack_message_ts + slack_message_channel queryable
- Strategy(quiet_hours_start="22:00", quiet_hours_end="07:00", proposal_timeout_minutes=15)
- Strategy(proposal_timeout_minutes=0) raises ValidationError (gt=0)
- Strategy(quiet_hours_start="bad") raises ValidationError
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pydantic

from gekko.db.models import (
    Proposal as ProposalRow,
    SlackActionDedup,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy
from decimal import Decimal


# ---------------------------------------------------------------------------
# ORM tests
# ---------------------------------------------------------------------------


async def _seed_user_and_proposal(
    session_factory: Any,
    *,
    user_id: str = "test-dedup-user",
) -> tuple[str, str]:
    """Seed a User + Strategy + Proposal row; return (strategy_id, proposal_id)."""
    strategy_id = "strat-" + uuid4().hex
    proposal_id = uuid4().hex
    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="test-strategy",
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status="PENDING",
                payload_json="{}",
                client_order_id=None,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )
    return strategy_id, proposal_id


@pytest.mark.asyncio
async def test_slack_action_dedup_orm(temp_sqlcipher_db: Any) -> None:
    """Insert a SlackActionDedup row and query it back."""
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-dedup-user"
    _, proposal_id = await _seed_user_and_proposal(sf, user_id=user_id)

    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        row = SlackActionDedup(
            proposal_id=proposal_id,
            action_id="approve_proposal",
            actor_slack_user_id="U_SLACK_123",
            actor_gekko_user_id=user_id,
            source="slack",
            slack_trigger_id="Iv1234567890",
            inserted_at=now,
            result="first_write",
        )
        session.add(row)

    async with sf() as session:
        from sqlalchemy import select

        result = (
            await session.execute(
                select(SlackActionDedup).where(
                    SlackActionDedup.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        assert result.action_id == "approve_proposal"
        assert result.source == "slack"
        assert result.result == "first_write"
        # slack_trigger_id excluded from __repr__ (T-03-01-03)
        repr_str = repr(result)
        assert "trigger_id" not in repr_str.lower() or "slack_trigger_id" not in repr_str
        assert "proposal_id" in repr_str


@pytest.mark.asyncio
async def test_user_quiet_hours_columns_queryable(temp_sqlcipher_db: Any) -> None:
    """User.quiet_hours_start, quiet_hours_end, timezone columns are queryable."""
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-qh-user"
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                quiet_hours_start="22:00:00",
                quiet_hours_end="07:00:00",
                timezone="America/New_York",
            )
        )

    async with sf() as session:
        from sqlalchemy import select

        u = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        assert u.quiet_hours_start == "22:00:00"
        assert u.quiet_hours_end == "07:00:00"
        assert u.timezone == "America/New_York"


@pytest.mark.asyncio
async def test_proposal_expires_at_queryable(temp_sqlcipher_db: Any) -> None:
    """Proposal.expires_at, slack_message_ts, slack_message_channel are queryable."""
    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-exp-user"
    strategy_id, proposal_id = await _seed_user_and_proposal(sf, user_id=user_id)
    now = datetime.now(UTC).isoformat()

    async with sf() as session, session.begin():
        from sqlalchemy import select

        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        row.expires_at = now
        row.slack_message_ts = "1234567890.000100"
        row.slack_message_channel = "D_CHANNEL_001"

    async with sf() as session:
        from sqlalchemy import select

        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.expires_at == now
        assert row.slack_message_ts == "1234567890.000100"
        assert row.slack_message_channel == "D_CHANNEL_001"


# ---------------------------------------------------------------------------
# Pydantic tests
# ---------------------------------------------------------------------------


def _make_strategy(**kwargs: Any) -> Strategy:
    defaults = dict(
        strategy_id="strat-001",
        user_id="chris",
        name="test",
        version=1,
        thesis="Bullish on AI infra.",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )
    defaults.update(kwargs)
    return Strategy(**defaults)


def test_strategy_quiet_hours_parses() -> None:
    """Strategy with quiet_hours_start + quiet_hours_end + proposal_timeout_minutes parses."""
    s = _make_strategy(
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
        proposal_timeout_minutes=15,
    )
    assert s.quiet_hours_start == "22:00"
    assert s.quiet_hours_end == "07:00"
    assert s.proposal_timeout_minutes == 15


def test_strategy_proposal_timeout_zero_raises() -> None:
    """Strategy(proposal_timeout_minutes=0) raises pydantic.ValidationError (gt=0)."""
    with pytest.raises(pydantic.ValidationError):
        _make_strategy(proposal_timeout_minutes=0)


def test_strategy_negative_timeout_raises() -> None:
    """Strategy(proposal_timeout_minutes=-1) raises pydantic.ValidationError (gt=0)."""
    with pytest.raises(pydantic.ValidationError):
        _make_strategy(proposal_timeout_minutes=-1)


def test_strategy_bad_quiet_hours_format_raises() -> None:
    """Strategy(quiet_hours_start='bad') raises pydantic.ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        _make_strategy(quiet_hours_start="bad")


def test_strategy_none_quiet_hours_ok() -> None:
    """Strategy with None quiet_hours_* parses (None is the default)."""
    s = _make_strategy()
    assert s.quiet_hours_start is None
    assert s.quiet_hours_end is None
    assert s.proposal_timeout_minutes is None
