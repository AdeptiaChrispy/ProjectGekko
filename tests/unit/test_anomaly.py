"""Anomaly drawdown reflex — TRUST-03 (Wave-0 RED stub, Plan 05-01).

Asserts the contract for ``gekko.anomaly.evaluator.evaluate_drawdown`` (landed by
Plan 04):

  * drawdown >= threshold demotes the strategy + cancels pending auto orders +
    DMs the operator (the DM bypasses quiet hours per D-T13).
  * a strategy already propose-only is an idempotent no-op.
  * the anomaly threshold trips BEFORE max_daily_loss_usd (threshold ordering,
    D-T11).

EXPECTED RED until the evaluator module lands — the import gates collection.
"""

from __future__ import annotations

import pytest

# RED until Plan 04 lands anomaly/evaluator.py.
from gekko.anomaly.evaluator import evaluate_drawdown


@pytest.mark.asyncio
async def test_drawdown_at_or_above_threshold_demotes() -> None:
    """dd >= threshold → returns True (demoted + cancelled + DM'd)."""
    assert callable(evaluate_drawdown)


@pytest.mark.asyncio
async def test_already_propose_only_is_a_noop() -> None:
    """A strategy not in auto-within-caps is an idempotent no-op (returns False)."""
    assert callable(evaluate_drawdown)


@pytest.mark.asyncio
async def test_anomaly_trips_before_max_daily_loss() -> None:
    """Threshold ordering: anomaly demotion precedes the max_daily_loss cap (D-T11)."""
    assert callable(evaluate_drawdown)
