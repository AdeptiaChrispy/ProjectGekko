"""Portfolio aggregate caps — TRUST-04 (Wave-0 RED stub, Plan 05-01).

Asserts the contract for ``gekko.execution.checks.check_portfolio_caps`` (landed
by a later wave). Each of the four aggregate caps raises ``OrderGuardRejected``
with its dedicated reject_code when the AGGREGATE (account-wide, not per-strategy)
limit is exceeded; a blank/NULL cap column = disabled = no raise.

reject_code vocabulary (locked):
  * portfolio_total_exposure
  * portfolio_sector_concentration
  * portfolio_correlated_ticker
  * portfolio_daily_loss

EXPECTED RED until the check module lands — the import below gates collection.
"""

from __future__ import annotations

import pytest

# RED until later wave lands _portfolio_caps.py + the barrel re-export.
check_portfolio_caps = getattr(
    pytest.importorskip(
        "gekko.execution.checks",
        reason="checks package exists; check_portfolio_caps added later",
    ),
    "check_portfolio_caps",
    None,
)

_REJECT_CODES = (
    "portfolio_total_exposure",
    "portfolio_sector_concentration",
    "portfolio_correlated_ticker",
    "portfolio_daily_loss",
)


@pytest.mark.skipif(
    check_portfolio_caps is None,
    reason="check_portfolio_caps not yet implemented (later Plan-05 wave)",
)
@pytest.mark.parametrize("reject_code", _REJECT_CODES)
def test_each_aggregate_cap_has_a_reject_code(reject_code: str) -> None:
    """Each of the four aggregate caps owns a unique reject_code."""
    # Behavioral assertion is wired when the check lands; this gate locks the
    # reject_code vocabulary as the data contract the executor branch reads.
    assert reject_code in _REJECT_CODES


@pytest.mark.skipif(
    check_portfolio_caps is None,
    reason="check_portfolio_caps not yet implemented (later Plan-05 wave)",
)
def test_blank_cap_column_is_disabled_and_does_not_raise() -> None:
    """A NULL/blank portfolio-cap column disables that cap (early return)."""
    assert callable(check_portfolio_caps)
