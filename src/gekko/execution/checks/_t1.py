"""T+1 settlement BLOCK check — Plan 02-03 Task 2 (D-29 / EXEC-11).

US equities settle T+1 as of May 2024 (SEC's shortened settlement cycle).
On a CASH account, unsettled proceeds from a SELL cannot be used to BUY a
different security without risking a Good Faith Violation (GFV). On a
MARGIN account, the broker extends credit against the unsettled
proceeds, so T+1 doesn't bind the same way.

Block conditions (RESEARCH §4):

  * Cash account (``shorting_enabled=False`` is the proxy for "cash
    account" — margin accounts have shorting enabled).
  * Side is ``BUY``.
  * ``qty * ref_price > non_marginable_buying_power``.

``non_marginable_buying_power`` is the Alpaca-verified field that
represents settled cash available to buy — T+1-aware.

``ref_price`` selection mirrors :mod:`gekko.execution.checks._qty_price`:

  * LIMIT -> ``req.limit_price``
  * STOP -> ``req.stop_price``
  * MARKET -> ``broker.get_quote(req.symbol).ask_price`` (falls back to
    ``ap`` for forward-compat with alpaca-py wire-shape variants)

References:
  * .planning/phases/02-orderguard.../02-RESEARCH.md  §4 (T+1 detection)
  * .planning/phases/02-orderguard.../02-PATTERNS.md  §1a row 9
  * SEC T+1 rule (May 2024) https://www.sec.gov/news/press-release/2024-29
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from gekko.brokers.base import Brokerage, OrderRequest
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType


def _resolve_ref_price(
    req: OrderRequest, quote: dict[str, Any] | None
) -> Decimal | None:
    """Pick the reference price for the order-cost calculation.

    Mirrors :func:`gekko.execution.checks._qty_price` selection logic.
    Returns ``None`` when no usable price is available.
    """
    if req.order_type is OrderType.LIMIT:
        return req.limit_price
    if req.order_type is OrderType.STOP:
        return req.stop_price
    # MARKET — use the quote's ask price.
    if quote is None:
        return None
    raw = quote.get("ask_price")
    if raw is None:
        raw = quote.get("ap")
    if raw is None:
        return None
    return Decimal(str(raw))


async def check_t1_settlement(
    *,
    req: OrderRequest,
    account: dict[str, Any],
    broker: Brokerage | None = None,
) -> None:
    """Block when a BUY would use unsettled proceeds on a cash account.

    :param req: The :class:`OrderRequest` about to be sent.
    :param account: Output of ``broker.get_account()``. Required keys:
        * ``non_marginable_buying_power`` (settled cash, T+1-aware)
        * ``shorting_enabled`` (proxy for margin-account discriminator)
    :param broker: The wrapped concrete :class:`Brokerage`. Used to fetch
        the ask quote for MARKET orders. ``None`` is allowed for tests
        that pre-stash ``last_quote_ask`` on the account dict.
    :raises OrderGuardRejected: ``reject_code='t1_settlement'`` when the
        order cost exceeds non_marginable_buying_power on a cash account.
    """
    # SELL doesn't have a T+1 constraint — proceeds aren't being spent.
    if req.side is not OrderSide.BUY:
        return

    # Margin accounts: the broker extends credit; T+1 isn't a hard bind.
    shorting_enabled = account.get("shorting_enabled") is True
    if shorting_enabled:
        return

    # Resolve ref_price.
    quote: dict[str, Any] | None = None
    if req.order_type is OrderType.MARKET:
        # Test path: account dict may carry last_quote_ask directly.
        cached = account.get("last_quote_ask")
        if cached is not None:
            quote = {"ask_price": cached}
        elif broker is not None:
            try:
                quote = await broker.get_quote(req.symbol)
            except Exception:  # noqa: BLE001 - best-effort
                quote = None

    ref_price = _resolve_ref_price(req, quote)
    if ref_price is None or ref_price <= Decimal("0"):
        # Cannot price the order — defer to check_qty_price_sanity which
        # raises ref_price_missing for the same input shape. Defense in
        # depth means don't double-reject here.
        return

    non_marginable_raw = account.get("non_marginable_buying_power")
    if non_marginable_raw is None:
        # Field absent (e.g., margin account that doesn't expose it) —
        # nothing to compare against; let downstream checks run.
        return
    non_marginable = Decimal(str(non_marginable_raw))

    order_cost = req.qty * ref_price
    if order_cost > non_marginable:
        raise OrderGuardRejected(
            "t1_settlement",
            (
                f"BUY order cost {order_cost} exceeds non-marginable "
                f"(settled) buying power {non_marginable}; T+1 settlement "
                f"cycle means unsettled proceeds cannot fund this BUY "
                f"without risking a Good Faith Violation on this cash "
                f"account"
            ),
            extra={
                "ticker": req.symbol,
                "order_cost": str(order_cost),
                "non_marginable_buying_power": str(non_marginable),
                "ref_price": str(ref_price),
            },
        )


__all__: tuple[str, ...] = ("check_t1_settlement",)
