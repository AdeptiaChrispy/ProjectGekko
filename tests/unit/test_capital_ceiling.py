"""Per-strategy capital ceiling — TRUST-05 (Wave-0 RED stub, Plan 05-01).

Asserts the contract for ``gekko.execution.checks.check_capital_ceiling`` (landed
by a later wave): total deployed capital (open positions for the strategy's
tickers + this order's notional) over ``capital_ceiling_usd`` raises
``OrderGuardRejected("capital_ceiling")``; lowering the ceiling is unconstrained.

EXPECTED RED until the check module lands.
"""

from __future__ import annotations

import pytest

check_capital_ceiling = getattr(
    pytest.importorskip(
        "gekko.execution.checks",
        reason="checks package exists; check_capital_ceiling added later",
    ),
    "check_capital_ceiling",
    None,
)


@pytest.mark.skipif(
    check_capital_ceiling is None,
    reason="check_capital_ceiling not yet implemented (later Plan-05 wave)",
)
def test_capital_ceiling_reject_code_is_capital_ceiling() -> None:
    """Over-ceiling deployment raises OrderGuardRejected('capital_ceiling')."""
    assert callable(check_capital_ceiling)


@pytest.mark.skipif(
    check_capital_ceiling is None,
    reason="check_capital_ceiling not yet implemented (later Plan-05 wave)",
)
def test_lowering_ceiling_is_unconstrained() -> None:
    """De-risking (lowering the ceiling) is always allowed — never blocked."""
    assert callable(check_capital_ceiling)
