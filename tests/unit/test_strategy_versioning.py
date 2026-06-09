"""Tests for snapshot-row versioning helper — Plan 01-06 Task 1 (D-05).

``next_version(session, *, user_id, strategy_name)`` returns ``max(version)+1``
for that ``(user_id, strategy_name)`` pair, or ``1`` if none. Used by Plan 01-09
strategy CRUD to compute the version of the NEXT inserted row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_next_version_first_save_returns_one(temp_sqlcipher_db: Any) -> None:
    from gekko.db.models import User
    from gekko.schemas.strategy import next_version

    factory = async_sessionmaker(temp_sqlcipher_db, expire_on_commit=False)
    async with factory() as session:  # type: AsyncSession
        session.add(
            User(
                user_id="alice",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        await session.flush()
        v = await next_version(session, user_id="alice", strategy_name="ai-infra")
        assert v == 1


@pytest.mark.asyncio
async def test_next_version_increments_after_each_save(temp_sqlcipher_db: Any) -> None:
    from gekko.db.models import Strategy as StrategyRow
    from gekko.db.models import User
    from gekko.schemas.strategy import next_version

    factory = async_sessionmaker(temp_sqlcipher_db, expire_on_commit=False)
    async with factory() as session:  # type: AsyncSession
        session.add(
            User(
                user_id="alice",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        await session.flush()

        for expected in (1, 2, 3):
            v = await next_version(
                session, user_id="alice", strategy_name="ai-infra"
            )
            assert v == expected
            # Persist a snapshot row so the next call sees a higher max.
            session.add(
                StrategyRow(
                    strategy_id=f"strat-{expected}",
                    user_id="alice",
                    strategy_name="ai-infra",
                    version=v,
                    payload_json="{}",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_next_version_scoped_by_user_and_strategy(temp_sqlcipher_db: Any) -> None:
    """Different ``(user_id, strategy_name)`` pairs increment independently."""
    from gekko.db.models import Strategy as StrategyRow
    from gekko.db.models import User
    from gekko.schemas.strategy import next_version

    factory = async_sessionmaker(temp_sqlcipher_db, expire_on_commit=False)
    async with factory() as session:  # type: AsyncSession
        session.add_all(
            [
                User(
                    user_id="alice",
                    created_at=datetime.now(timezone.utc).isoformat(),
                ),
                User(
                    user_id="bob",
                    created_at=datetime.now(timezone.utc).isoformat(),
                ),
            ]
        )
        await session.flush()

        # alice/ai-infra has 2 versions
        for v in (1, 2):
            session.add(
                StrategyRow(
                    strategy_id=f"alice-ai-{v}",
                    user_id="alice",
                    strategy_name="ai-infra",
                    version=v,
                    payload_json="{}",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        await session.flush()

        assert (
            await next_version(session, user_id="alice", strategy_name="ai-infra")
        ) == 3
        # Different strategy name resets.
        assert (
            await next_version(session, user_id="alice", strategy_name="energy")
        ) == 1
        # Different user resets.
        assert (
            await next_version(session, user_id="bob", strategy_name="ai-infra")
        ) == 1


@pytest.mark.asyncio
async def test_payload_json_roundtrips_via_model_validate_json(
    temp_sqlcipher_db: Any,
) -> None:
    """Each saved payload survives a ``Strategy.model_validate_json`` round-trip."""
    from decimal import Decimal

    from gekko.db.models import Strategy as StrategyRow
    from gekko.db.models import User
    from gekko.schemas.strategy import HardCaps, Strategy, next_version

    factory = async_sessionmaker(temp_sqlcipher_db, expire_on_commit=False)
    async with factory() as session:  # type: AsyncSession
        session.add(
            User(
                user_id="alice",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        await session.flush()

        for i in (1, 2, 3):
            v = await next_version(
                session, user_id="alice", strategy_name="ai-infra"
            )
            strategy = Strategy(
                strategy_id=f"strat-{i}",
                user_id="alice",
                name="ai-infra",
                version=v,
                thesis="Bullish on AI infra.",
                watchlist=["NVDA", "AMD"],
                hard_caps=HardCaps(
                    max_position_pct=Decimal("0.05"),
                    max_daily_loss_usd=Decimal("200"),
                    max_trades_per_day=3,
                    max_sector_exposure_pct=Decimal("0.25"),
                ),
                schedule_time=None,
                mode="paper",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
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
            await session.flush()

        # Now load each row back and round-trip via Pydantic
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(StrategyRow)
                .where(StrategyRow.user_id == "alice")
                .order_by(StrategyRow.version)
            )
        ).scalars().all()
        assert len(rows) == 3
        for r in rows:
            parsed = Strategy.model_validate_json(r.payload_json)
            assert parsed.version == r.version
            assert parsed.name == r.strategy_name
