"""Anomaly auto-demotion reflex — Plan 05-04 (TRUST-04 / SC-4).

The single-day-drawdown early-warning that removes a strategy's autonomy
*before* the hard per-strategy ``max_daily_loss_usd`` cap halts trading. On
breach the reflex demotes the strategy to ``propose-only`` (via the AST-gated
``trust.demote_strategy_from_auto``), cancels the strategy's pending auto-orders
(open broker orders + PENDING auto-proposals), writes an ``anomaly_demotion``
audit event, and fires an urgent Slack DM that bypasses quiet hours.

Surgical (touches only the offending strategy — D-T12), idempotent (a strategy
already ``propose-only`` is a no-op — mirrors the set-once stamp), and Decimal-
exact (no float in the drawdown math).
"""

from __future__ import annotations

from gekko.anomaly.evaluator import evaluate_drawdown

__all__: tuple[str, ...] = ("evaluate_drawdown",)
