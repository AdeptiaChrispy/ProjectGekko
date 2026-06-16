"""Qty × ref_price 2% drift check — Plan 02-02 Task 2 (D-27 / EXEC-04).

The off-by-magnitude defense per CONTEXT.md D-27. The LLM declares its dollar
intent via ``TradeProposal.target_notional_usd``; OrderGuard recomputes
``qty × ref_price`` and rejects when the drift exceeds 2%. If the LLM is
wrong by 10x in ``qty`` OR ``limit_price`` (but not both — a coordinated
attack is out of scope), this check fires.

``ref_price`` selection by order_type:

  * LIMIT  -> ``req.limit_price`` (the LLM's stated price)
  * STOP   -> ``req.stop_price`` (the LLM's stop trigger)
  * MARKET -> ``broker.get_quote(symbol).ask_price`` (closest we can get
    to the executable price without a fill; ``ap`` is the alpaca-py 0.43
    key, but we accept ``ask_price`` for forward-compat with future SDK
    versions)

Decimal-exact math throughout per PATTERNS §3b (the EXEC-01 / D-20
binary-fp ban). The 2% literal is ``Decimal("0.02")`` — NEVER the bare
``0.02`` literal.
"""

from __future__ import annotations

from decimal import Decimal

from gekko.brokers.base import Brokerage, OrderRequest
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderType


async def check_qty_price_sanity(
    *,
    req: OrderRequest,
    target_notional_usd: Decimal,
    broker: Brokerage,
) -> None:
    """Reject when ``req.qty × ref_price`` drifts more than 2% from declared.

    :param req: The :class:`OrderRequest`. ``order_type`` selects ``ref_price``.
    :param target_notional_usd: The :class:`TradeProposal.target_notional_usd`
        the LLM authored. Comparison baseline.
    :param broker: The wrapped concrete :class:`Brokerage`. For MARKET orders
        we call ``broker.get_quote(req.symbol)`` to derive ``ref_price`` from
        the latest ask.
    :raises OrderGuardRejected:

        * ``reject_code='ref_price_missing'`` — LIMIT with no ``limit_price``,
          STOP with no ``stop_price``, or quote returned no ask/bid.
        * ``reject_code='qty_price_drift'`` — drift > 2%.
    """
    ref_price: Decimal | None
    if req.order_type is OrderType.LIMIT:
        ref_price = req.limit_price
    elif req.order_type is OrderType.STOP:
        ref_price = req.stop_price
    elif req.order_type is OrderType.MARKET:
        quote = await broker.get_quote(req.symbol)
        # alpaca-py 0.43 returns the StockLatestQuote Pydantic model dumped as
        # a dict; ``ask_price`` is the documented key. Some test mocks use
        # the shorter ``ap`` key (alpaca-py v1 historical wire shape) — accept
        # both for resilience.
        raw = quote.get("ask_price")
        if raw is None:
            raw = quote.get("ap")
        if raw is None:
            ref_price = None
        else:
            ref_price = Decimal(str(raw))
    else:  # pragma: no cover - exhaustive enum guard
        msg = f"unsupported order_type for qty_price check: {req.order_type!r}"
        raise OrderGuardRejected(
            "ref_price_missing",
            msg,
            extra={"ticker": req.symbol, "order_type": str(req.order_type)},
        )

    if ref_price is None or ref_price <= Decimal("0"):
        raise OrderGuardRejected(
            "ref_price_missing",
            (
                f"Cannot compute ref_price for {req.symbol!r} "
                f"order_type={str(req.order_type)!r}; ref_price={ref_price!r}"
            ),
            extra={
                "ticker": req.symbol,
                "order_type": str(req.order_type),
            },
        )

    actual_notional = req.qty * ref_price
    drift_abs = abs(actual_notional - target_notional_usd)
    # target_notional_usd is Decimal(..., gt=Decimal("0")) per the Pydantic
    # schema validator (plan 02-01 Task 3), so division is safe.
    drift_pct = drift_abs / target_notional_usd
    if drift_pct > Decimal("0.02"):
        raise OrderGuardRejected(
            "qty_price_drift",
            (
                f"qty × ref_price ({req.qty} × {ref_price} = {actual_notional}) "
                f"drifts {drift_pct * Decimal('100'):.4f}% from "
                f"target_notional_usd {target_notional_usd}; max allowed 2%"
            ),
            extra={
                "ticker": req.symbol,
                "ref_price": str(ref_price),
                "actual_notional": str(actual_notional),
                "target_notional_usd": str(target_notional_usd),
                "drift_pct": str(drift_pct),
            },
        )


__all__: tuple[str, ...] = ("check_qty_price_sanity",)
