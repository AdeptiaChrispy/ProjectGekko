"""Market-hours defense-in-depth check — Plan 02-02 Task 1 (PATTERNS §1a row 11).

Phase-1's executor (``execute_proposal``) already checks ``is_market_open()``
at lines 188-220 BEFORE constructing the OrderRequest, and transitions
``APPROVED → FAILED`` with an ``executor.market_closed`` audit event when
the market is closed. OrderGuard re-checks at ``place_order`` time as defense
in depth — state may have changed between the executor's check and the
broker POST (e.g., scheduled session boundary crossing during a long-running
order build).

In the happy path the executor's check fires first and OrderGuard's is a
no-op. In the edge case where the market closes between executor's check
and OrderGuard's check, this raises ``OrderGuardRejected('market_closed', ...)``
and the cap_rejection state-transition branch in the executor catches it.
"""

from __future__ import annotations

from gekko.brokers.base import OrderRequest
from gekko.core.errors import OrderGuardRejected
from gekko.execution.market_hours import is_market_open


async def check_market_hours(req: OrderRequest) -> None:
    """Reject when the NYSE is outside regular trading hours.

    :param req: The :class:`OrderRequest` (carried for the ``ticker`` field
        in the audit-log payload).
    :raises OrderGuardRejected: With ``reject_code='market_closed'`` when
        :func:`gekko.execution.market_hours.is_market_open` returns False.
    """
    if not is_market_open():
        raise OrderGuardRejected(
            "market_closed",
            "NYSE not in regular trading hours",
            extra={"ticker": req.symbol},
        )


__all__: tuple[str, ...] = ("check_market_hours",)
