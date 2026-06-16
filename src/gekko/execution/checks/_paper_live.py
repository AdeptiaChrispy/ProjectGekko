"""Paper/live three-way invariant check — Plan 02-02 Task 1 (D-32 / EXEC-05).

The three-way invariant per RESEARCH §2:

    strategy.mode == "live"  ⇔  account_mode == "LIVE"  ⇔  broker.is_paper is False
    strategy.mode == "paper" ⇔  account_mode == "PAPER" ⇔  broker.is_paper is True

A future bug that flips one of these without the others — env-var typo,
credential-rotation swap, alpaca-py base_url change — gets caught here BEFORE
the order POST fires. Knight-Capital insurance for the paper-vs-live mix-up.

Pure deterministic check (no IO). Plan 02-06 extends with a fourth axis:
``BrokerCredential.kind`` discriminator (alpaca_paper vs alpaca_live) — that
gate uses the same ``OrderGuardRejected("paper_live_mismatch_credential", ...)``
shape so the audit-log surface is consistent across waves.
"""

from __future__ import annotations

from gekko.brokers.base import Brokerage
from gekko.core.errors import OrderGuardRejected


def check_paper_live_pairing(
    *,
    broker: Brokerage,
    strategy_mode: str,
    account_mode: str,
    user_id: str,
) -> None:
    """Reject any mismatch between strategy mode, account mode, and broker.

    :param broker: The concrete :class:`Brokerage` (NOT the OrderGuard wrap —
        callers pass ``self._wrapped`` so the ``is_paper`` introspection sees
        the truthful underlying broker).
    :param strategy_mode: ``"paper"`` or ``"live"`` from ``Strategy.mode``.
    :param account_mode: ``"PAPER"`` or ``"LIVE"`` — the value stamped on the
        proposal row at proposal-build time (BLOCKER #5 / plan 02-01 Task 3).
    :param user_id: Carried into ``extra`` for audit-log filtering.
    :raises OrderGuardRejected: With ``reject_code='paper_live_mismatch_broker'``
        when ``broker.is_paper`` disagrees with ``strategy_mode``; with
        ``reject_code='paper_live_mismatch_account'`` when ``account_mode``
        disagrees with ``strategy_mode``.
    """
    expected_paper = strategy_mode == "paper"
    if broker.is_paper is not expected_paper:
        raise OrderGuardRejected(
            "paper_live_mismatch_broker",
            (
                f"strategy.mode={strategy_mode!r} expects "
                f"broker.is_paper={expected_paper!r}, found "
                f"broker.is_paper={broker.is_paper!r}"
            ),
            extra={
                "strategy_mode": strategy_mode,
                "broker_is_paper": broker.is_paper,
                "user_id": user_id,
            },
        )

    expected_account = "PAPER" if expected_paper else "LIVE"
    if account_mode != expected_account:
        raise OrderGuardRejected(
            "paper_live_mismatch_account",
            (
                f"strategy.mode={strategy_mode!r} expects "
                f"account_mode={expected_account!r}, found "
                f"account_mode={account_mode!r}"
            ),
            extra={
                "strategy_mode": strategy_mode,
                "account_mode": account_mode,
                "user_id": user_id,
            },
        )


__all__: tuple[str, ...] = ("check_paper_live_pairing",)
