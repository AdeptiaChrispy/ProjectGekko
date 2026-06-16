"""OrderGuard check functions — Plan 02-02 Task 1 + Task 2 / Plan 02-03 Task 2 + Task 3.

Per RESEARCH §1 (architecture: decorator-over-Brokerage / D-26) and PATTERNS §1a:
one module per check so each is independently unit-testable, then re-exported
here for the caller-facing import (``from gekko.execution.checks import
check_universe``).

The eight BLOCK checks + one FLAG helper:

  * :func:`check_universe` — ticker ∈ ``strategy.watchlist`` (D-29 / EXEC-04)
  * :func:`check_hard_caps` — 4 hard caps (D-29 / EXEC-04)
  * :func:`check_qty_price_sanity` — 2% drift bound on qty × ref_price (D-27)
  * :func:`check_paper_live_pairing` — 3-way ``mode ⇔ account_mode ⇔ is_paper``
    invariant (D-32 / EXEC-05)
  * :func:`check_kill_switch` — read-only DB check on ``users.kill_active``
    (D-35; write side lives in plan 02-05)
  * :func:`check_market_hours` — re-uses Phase-1 ``is_market_open``
    (defense in depth — executor's existing market_closed branch fires first;
    OrderGuard's check is a no-op in that case)
  * :func:`check_pdt` — Pattern Day Trader BLOCK (D-29 / EXEC-11; plan 02-03)
  * :func:`check_t1_settlement` — T+1 unsettled-cash BLOCK on cash accounts
    (D-29 / EXEC-11; plan 02-03)

  * :func:`flag_wash_sale` — wash-sale 30-day lookback FLAG (D-28 / EXEC-09;
    plan 02-03). Returns ``dict | None``. NEVER raises. OrderGuard does NOT
    call this — ProposalWriter attaches the flag at proposal-build time.

Each BLOCK check raises :class:`gekko.core.errors.OrderGuardRejected` (with a
machine-readable ``reject_code`` plus ``reject_reason`` + ``extra`` dict)
on failure, or returns ``None`` on pass. Callers (OrderGuard.place_order)
call them sequentially; the first rejection short-circuits.

Plans 02-05 + 02-06 extend kill_switch / paper_live with WRITE-side +
credential-kind hardening.
"""

from __future__ import annotations

from gekko.execution.checks._hard_caps import check_hard_caps
from gekko.execution.checks._kill_switch import check_kill_switch
from gekko.execution.checks._market_hours import check_market_hours
from gekko.execution.checks._paper_live import check_paper_live_pairing
from gekko.execution.checks._pdt import check_pdt
from gekko.execution.checks._qty_price import check_qty_price_sanity
from gekko.execution.checks._t1 import check_t1_settlement
from gekko.execution.checks._universe import check_universe
from gekko.execution.checks._wash_sale import flag_wash_sale

__all__: tuple[str, ...] = (
    "check_hard_caps",
    "check_kill_switch",
    "check_market_hours",
    "check_paper_live_pairing",
    "check_pdt",
    "check_qty_price_sanity",
    "check_t1_settlement",
    "check_universe",
    "flag_wash_sale",
)
