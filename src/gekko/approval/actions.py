"""Shared approval helpers — Plan 03-05 Task 3 (D-54), updated Plan 03-11.

``_drift_check`` is the agent's own output-consistency guard (D-27 / T-03-05-07).
It is applied when the AGENT produces a quantity. It is NOT applied when the
OPERATOR deliberately resizes — that made edit-size useless (even 47→50 shares,
~6% change, was rejected with a cryptic drift error).

``_check_edit_size_caps`` is the operator-edit gate (Plan 03-11). It validates
the edited qty against the strategy's OrderGuard hard caps:
  max_position_pct * account_equity = max order notional in dollars.
Both the Slack modal view_submission handler and the dashboard
POST /approvals/{id}/edit-submit call this helper so validation cannot diverge.

The Knight Capital defense is preserved: edit-size always flows
  PENDING → APPROVED → execute_proposal (which calls OrderGuard + broker).
  No path in edit-size calls place_order directly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gekko.schemas.strategy import Strategy

__all__: tuple[str, ...] = ("_check_edit_size_caps", "_drift_check")

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


def _check_edit_size_caps(
    qty: Decimal,
    ref_price: Decimal,
    strategy: "Strategy",
    account_equity: Decimal,
) -> tuple[bool, str]:
    """Validate an operator-edited qty against the strategy's OrderGuard hard caps.

    This is the sole gate for operator edit-size submissions (Plan 03-11).
    ``_drift_check`` is NOT called here — it remains the agent's own output-
    consistency guard (D-27) and is applied only when the agent proposes a qty.

    The Knight Capital defense is the absolute dollar cap:
      max_order_notional = strategy.hard_caps.max_position_pct * account_equity

    The function is synchronous. The caller (Slack handler and dashboard route)
    fetches account_equity asynchronously before calling this helper.

    Args:
        qty: Proposed new quantity (operator input — untrusted; must be > 0).
        ref_price: Reference price fetched server-side (trusted).
        strategy: The strategy whose HardCaps define the cap bounds.
        account_equity: Account equity fetched from the broker (trusted).
            When 0 (paper account with no funding), the cap check is skipped
            (fail-open) because the cap cannot be computed without equity.
            OrderGuard still fires at execute_proposal time as the last line.

    Returns:
        ``(True, "")`` when the qty passes all checks.
        ``(False, plain_msg)`` with a user-facing error when a check fails.
    """
    # Hard cap 0 — minimum qty
    if qty <= Decimal("0"):
        return (False, "Quantity must be at least 1 share.")

    # Hard cap 1 — max position size in dollars
    # Skip when equity == 0: paper account may report 0 before first funding.
    # Fail-open here; OrderGuard re-checks at execute_proposal time.
    if account_equity > Decimal("0"):
        max_order_notional = strategy.hard_caps.max_position_pct * account_equity
        if max_order_notional > Decimal("0"):
            new_notional = qty * ref_price
            if new_notional > max_order_notional:
                max_shares_approx = (
                    int(max_order_notional / ref_price)
                    if ref_price > Decimal("0")
                    else 0
                )
                return (
                    False,
                    (
                        f"That's above your max of ${max_order_notional:,.2f}"
                        f" (~{max_shares_approx} shares) — pick a smaller number."
                    ),
                )

    return (True, "")
