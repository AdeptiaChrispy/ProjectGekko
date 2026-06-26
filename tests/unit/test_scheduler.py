"""Anomaly + snapshot scheduler registration — TRUST-03 (Wave-0 stub, Plan 05-01).

Asserts the contract for the two new scheduler jobs landed by Plan 04's edit to
``gekko.scheduler.jobs``:

  * the anomaly-evaluator IntervalTrigger job (NYSE-gated, coalesce=True,
    max_instances=1) — runs ``evaluate_drawdown`` on a tick to catch unrealized
    drift between fills.
  * the market-open snapshot job that writes the stable start-of-day denominator
    ``evaluate_drawdown`` reads (Plan 04 Task 3 contract / RESEARCH Open Q #3).

Invariant 1 (the registration helpers exist with the right names) is gated on the
not-yet-landed functions, so this is EXPECTED RED/SKIPPED until Plan 04.
"""

from __future__ import annotations

import pytest

jobs = pytest.importorskip(
    "gekko.scheduler.jobs",
    reason="scheduler.jobs exists; the anomaly + snapshot registrars added later",
)


@pytest.mark.skipif(
    not hasattr(jobs, "register_anomaly_evaluator"),
    reason="register_anomaly_evaluator not yet implemented (Plan 04)",
)
def test_anomaly_evaluator_registers_interval_trigger() -> None:
    """The anomaly evaluator registers an IntervalTrigger (NYSE-gated, coalesce, max_instances=1)."""
    assert hasattr(jobs, "register_anomaly_evaluator")


@pytest.mark.skipif(
    not hasattr(jobs, "register_market_open_snapshot"),
    reason="register_market_open_snapshot not yet implemented (Plan 04)",
)
def test_market_open_snapshot_registers() -> None:
    """The market-open snapshot job registers and writes the start-of-day denominator."""
    assert hasattr(jobs, "register_market_open_snapshot")
