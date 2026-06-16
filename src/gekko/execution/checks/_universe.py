"""Universe-whitelist check — Plan 02-02 Task 1 (D-29 / EXEC-04).

OrderGuard's first cheap in-memory rejection: if the proposed ticker is not in
``strategy.watchlist``, reject the order BEFORE any DB or network round-trip.
Phase 1's ``ProposalWriter.merge_then_validate`` already catches hallucinated
tickers at proposal-build time; this is the defense-in-depth at place_order
time per D-29 — the watchlist may have changed between propose-time (T0) and
approve-click (T1).

Pure-Python in-memory check; no DB, no broker call. The watchlist is the
already-normalized list on the Strategy Pydantic model (the `_normalize_watchlist`
validator on ``Strategy.watchlist`` uppercases + strips at validation time, so
the comparison here is case-sensitive against already-upper tickers).
"""

from __future__ import annotations

from gekko.brokers.base import OrderRequest
from gekko.core.errors import OrderGuardRejected
from gekko.schemas.strategy import Strategy


async def check_universe(req: OrderRequest, *, strategy: Strategy) -> None:
    """Reject when ``req.symbol`` is not in ``strategy.watchlist``.

    :param req: The :class:`OrderRequest` OrderGuard is about to submit.
    :param strategy: The :class:`Strategy` snapshot the proposal was authored
        against. Its ``watchlist`` is the authoritative universe for this
        order.
    :raises OrderGuardRejected: When ``req.symbol`` is missing from
        ``strategy.watchlist``. ``reject_code='universe'``.
    """
    if req.symbol not in strategy.watchlist:
        raise OrderGuardRejected(
            "universe",
            (
                f"ticker {req.symbol!r} not in strategy.watchlist "
                f"{list(strategy.watchlist)!r}"
            ),
            extra={
                "ticker": req.symbol,
                "watchlist": list(strategy.watchlist),
            },
        )


__all__: tuple[str, ...] = ("check_universe",)
