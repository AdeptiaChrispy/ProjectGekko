"""Shared approval helpers — Plan 03-05 Task 3 (D-54).

``_drift_check`` is the single source-of-truth for the OrderGuard 2% drift
invariant (D-27 / T-03-05-07). Both the Slack modal view_submission handler
and the dashboard POST /approvals/{id}/edit-submit call this helper so the
two surfaces cannot drift in their drift math.

The Knight Capital defense is preserved: edit-size always flows
  PENDING → APPROVED → execute_proposal (which calls OrderGuard + broker).
  No path in edit-size calls place_order directly.
"""

from __future__ import annotations

from decimal import Decimal

__all__: tuple[str, ...] = ("_drift_check",)

# D-27 / D-54 invariant: operator-supplied qty rejected if notional drift
# from target_notional_usd exceeds this threshold.
_DRIFT_THRESHOLD = Decimal("0.02")


def _drift_check(
    qty: Decimal,
    ref_price: Decimal,
    target_notional_usd: Decimal,
) -> Decimal:
    """Return the absolute drift fraction for a candidate qty.

    Drift = abs(qty * ref_price - target_notional_usd) / target_notional_usd

    The caller compares the returned value to ``Decimal("0.02")`` (the 2%
    threshold from D-27 / OrderGuard).  Returns the raw fraction; the
    caller decides whether to reject.

    Args:
        qty: Proposed new quantity (operator input — untrusted).
        ref_price: Reference price fetched server-side (trusted).
        target_notional_usd: Target notional from the TradeProposal (trusted).

    Returns:
        Absolute drift as a Decimal fraction (e.g. ``Decimal("0.015")`` for 1.5%).

    Raises:
        ZeroDivisionError: If target_notional_usd is zero (caller should guard).
    """
    new_notional = qty * ref_price
    drift_pct = abs(new_notional - target_notional_usd) / target_notional_usd
    return drift_pct
