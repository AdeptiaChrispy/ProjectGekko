"""Clean-streak derivation — TRUST-01 / TRUST-02 (Wave-0 RED stub, Plan 05-01).

These tests assert the contract for ``gekko.strategy.streak.compute_clean_streak``
(landed by Plan 02). They are EXPECTED RED until that module exists — the import
below fails to collect until then, which is the Nyquist scaffold.

Contract (RESEARCH Pattern 4 / D-T01..D-T05):
  * approvals partition by (strategy_name, account_mode) — cross-strategy and
    paper-vs-live approvals do NOT bleed into each other.
  * a cap_rejection mid-window zeroes the streak (D-T02).
  * a trust_demoted / anomaly_demotion / material-edit boundary resets the
    window (D-T03/D-T05).
  * StreakResult carries: clean_count, threshold (=10), eligible, block_reason,
    last_breach_date, last_reset_date.
"""

from __future__ import annotations

import pytest
import pytest_asyncio  # noqa: F401  (ensures async fixtures resolve)
from sqlalchemy.ext.asyncio import AsyncSession

# RED until Plan 02 lands streak.py — intentional collection failure.
from gekko.strategy.streak import StreakResult, compute_clean_streak


@pytest.mark.asyncio
async def test_ten_clean_approvals_make_strategy_eligible(
    temp_sqlcipher_db, seed_approval_events
) -> None:
    """10 clean approvals for one (strategy, mode) → eligible (threshold=10)."""
    async with AsyncSession(temp_sqlcipher_db) as s, s.begin():
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="PAPER", n=10
        )
    async with AsyncSession(temp_sqlcipher_db) as s:
        result: StreakResult = await compute_clean_streak(
            session=s, user_id="u1", strategy_name="alpha", account_mode="PAPER"
        )
    assert result.threshold == 10
    assert result.clean_count == 10
    assert result.eligible is True


@pytest.mark.asyncio
async def test_cap_rejection_zeroes_the_streak(
    temp_sqlcipher_db, seed_approval_events, seed_cap_rejection
) -> None:
    """A cap_rejection mid-window resets clean_count to count-since-breach."""
    async with AsyncSession(temp_sqlcipher_db) as s, s.begin():
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="PAPER", n=8
        )
        await seed_cap_rejection(
            s, user_id="u1", strategy_name="alpha",
            reject_code="hard_cap_position_pct",
        )
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="PAPER", n=2
        )
    async with AsyncSession(temp_sqlcipher_db) as s:
        result = await compute_clean_streak(
            session=s, user_id="u1", strategy_name="alpha", account_mode="PAPER"
        )
    assert result.clean_count == 2
    assert result.eligible is False
    assert result.last_breach_date is not None


@pytest.mark.asyncio
async def test_cross_strategy_approvals_do_not_bleed(
    temp_sqlcipher_db, seed_approval_events
) -> None:
    """Approvals on strategy 'beta' must not count toward 'alpha' (D-T05)."""
    async with AsyncSession(temp_sqlcipher_db) as s, s.begin():
        await seed_approval_events(
            s, user_id="u1", strategy_name="beta", account_mode="PAPER", n=10
        )
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="PAPER", n=3
        )
    async with AsyncSession(temp_sqlcipher_db) as s:
        result = await compute_clean_streak(
            session=s, user_id="u1", strategy_name="alpha", account_mode="PAPER"
        )
    assert result.clean_count == 3
    assert result.eligible is False


@pytest.mark.asyncio
async def test_paper_and_live_counted_separately(
    temp_sqlcipher_db, seed_approval_events
) -> None:
    """PAPER approvals do not count toward the LIVE streak (D-T01)."""
    async with AsyncSession(temp_sqlcipher_db) as s, s.begin():
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="PAPER", n=10
        )
        await seed_approval_events(
            s, user_id="u1", strategy_name="alpha", account_mode="LIVE", n=4
        )
    async with AsyncSession(temp_sqlcipher_db) as s:
        live = await compute_clean_streak(
            session=s, user_id="u1", strategy_name="alpha", account_mode="LIVE"
        )
    assert live.clean_count == 4
    assert live.eligible is False
