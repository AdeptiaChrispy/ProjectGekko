"""OrderGuard check functions — Plan 02-02 Task 1 + Task 2.

Per RESEARCH §1 (architecture: decorator-over-Brokerage / D-26) and PATTERNS §1a:
one module per check so each is independently unit-testable, then re-exported
here for the caller-facing import (``from gekko.execution.checks import
check_universe``).

The six BLOCK checks shipped by plan 02-02:

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

Each check raises :class:`gekko.core.errors.OrderGuardRejected` (with a
machine-readable ``reject_code`` plus ``reject_reason`` + ``extra`` dict)
on failure, or returns ``None`` on pass. Callers (OrderGuard.place_order)
call them sequentially; the first rejection short-circuits.

The PDT / T+1 / wash-sale checks come in plan 02-03; plans 02-05 + 02-06
extend kill_switch / paper_live with WRITE-side + credential-kind hardening.
"""

from __future__ import annotations

from gekko.execution.checks._hard_caps import check_hard_caps
from gekko.execution.checks._kill_switch import check_kill_switch
from gekko.execution.checks._market_hours import check_market_hours
from gekko.execution.checks._paper_live import check_paper_live_pairing
from gekko.execution.checks._qty_price import check_qty_price_sanity
from gekko.execution.checks._universe import check_universe

__all__: tuple[str, ...] = (
    "check_hard_caps",
    "check_kill_switch",
    "check_market_hours",
    "check_paper_live_pairing",
    "check_qty_price_sanity",
    "check_universe",
)
