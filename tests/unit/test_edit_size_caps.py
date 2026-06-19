"""Unit tests for _check_edit_size_caps — Plan 03-11 Task 1.

Validates the cap-based gate that replaced the 2% drift check for operator
edit-size submissions. Assertion: operator edits are validated against
strategy.hard_caps.max_position_pct * account_equity, NOT against the 2%
drift from target_notional_usd.

No claude_agent_sdk import anywhere in this module (grep gate).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from gekko.approval.actions import _check_edit_size_caps
from gekko.schemas.strategy import HardCaps, Strategy


# ---------------------------------------------------------------------------
# Helper — build a minimal Strategy with given max_position_pct
# ---------------------------------------------------------------------------


def _make_strategy(max_position_pct: Decimal) -> Strategy:
    """Return a minimal valid Strategy with the given position-pct cap."""
    return Strategy.model_validate(
        {
            "strategy_id": "strat-test-01",
            "user_id": "chris",
            "name": "Test Strategy",
            "version": 1,
            "thesis": "Test thesis for cap tests.",
            "watchlist": ["AAPL"],
            "hard_caps": HardCaps(
                max_position_pct=max_position_pct,
                max_daily_loss_usd=Decimal("500"),
                max_trades_per_day=5,
                max_sector_exposure_pct=Decimal("0.50"),
            ),
            "created_at": "2026-06-19T00:00:00+00:00",
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pass_within_cap() -> None:
    """50 shares at $200 = $10,000; cap = 0.20 * $60,000 = $12,000 → passes."""
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("50"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("60000"),
    )
    assert ok is True
    assert msg == ""


def test_fail_exceeds_cap() -> None:
    """500 shares at $200 = $100,000; cap = 0.20 * $50,000 = $10,000 → fails."""
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("500"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("50000"),
    )
    assert ok is False
    assert "That's above your max" in msg


def test_fail_zero_qty() -> None:
    """qty == 0 → rejected regardless of caps."""
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("0"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("60000"),
    )
    assert ok is False
    assert "Quantity must be at least 1" in msg


def test_fail_negative_qty() -> None:
    """Negative qty → rejected."""
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("-5"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("60000"),
    )
    assert ok is False


def test_pass_exact_cap() -> None:
    """qty * ref_price == max_order_notional exactly → passes (boundary is strict >)."""
    # 60 shares * $200 = $12,000; cap = 0.20 * $60,000 = $12,000 exactly
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("60"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("60000"),
    )
    assert ok is True
    assert msg == ""


def test_zero_equity_skip() -> None:
    """When equity == 0 the cap cannot be computed → fail-open (ok=True).

    Paper accounts that return 0 equity (fresh account, no funding yet)
    should not block all edits. OrderGuard still fires at execute_proposal
    time — the cap check here is the early-reject UI gate, not the last line.
    """
    strategy = _make_strategy(Decimal("0.20"))
    ok, msg = _check_edit_size_caps(
        qty=Decimal("500"),
        ref_price=Decimal("200"),
        strategy=strategy,
        account_equity=Decimal("0"),
    )
    assert ok is True
