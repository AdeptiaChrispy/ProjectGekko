"""``AlpacaBroker`` — paper-only Phase 1 broker — Plan 01-05 Task 2 + 3.

Per CONTEXT.md D-24: alpaca-py is the chosen SDK and Phase 1 only ever talks
to paper. Live trading is physically rejected at construction time, with two
defense layers:

1. **Argument check.** ``AlpacaBroker(paper=False)`` raises
   ``BrokerConfigError`` IMMEDIATELY, before any ``TradingClient`` is built.
   This is the load-bearing P1 invariant — Knight Capital insurance per
   Pitfall 7 (paper-vs-live mix-up).

2. **Post-construct probe.** After building the TradingClient, we read
   ``client._base_url`` (alpaca-py 0.43 exposes the ``BaseURL`` enum). If
   the URL does not look paper-y, raise — defense against a future
   alpaca-py change that flips the semantics of ``paper=`` or a corrupted
   env that swaps paper keys for live ones silently.

The async method implementations land in Task 3; Task 2's surface is just
the constructor + class attributes. The abstract methods are still wired
(otherwise the class would fail to instantiate per the ABC contract) but
their bodies are minimal placeholders that Task 3 fills in.

References:
  * CONTEXT.md D-24 — alpaca-py paper-only in P1
  * PITFALLS.md Pitfall 7 — paper-vs-live mix-up
  * PITFALLS.md Pitfall 4 — Knight Capital duplicate-order prevention
  * RESEARCH.md §"Code Examples — AlpacaBroker"
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopOrderRequest,
)

from gekko.brokers._retry import retry_on_rate_limit
from gekko.brokers.base import Brokerage, OrderRequest, OrderResult
from gekko.core.errors import BrokerConfigError, BrokerOrderError
from gekko.core.types import OrderSide, OrderType, TimeInForce

# ---------------------------------------------------------------------------
# Enum mappings — Gekko enum -> alpaca-py enum
# ---------------------------------------------------------------------------

_SIDE_MAP: dict[OrderSide, AlpacaOrderSide] = {
    OrderSide.BUY: AlpacaOrderSide.BUY,
    OrderSide.SELL: AlpacaOrderSide.SELL,
}

_TIF_MAP: dict[TimeInForce, AlpacaTimeInForce] = {
    TimeInForce.DAY: AlpacaTimeInForce.DAY,
    TimeInForce.GTC: AlpacaTimeInForce.GTC,
}


# ---------------------------------------------------------------------------
# AlpacaBroker
# ---------------------------------------------------------------------------


class AlpacaBroker(Brokerage):
    """Paper-only Alpaca broker.

    Constructor enforces ``paper=True`` via two layers (see module docstring).
    All sync alpaca-py calls are wrapped in ``asyncio.to_thread`` because the
    SDK has no native async API as of 0.43 — the wrapper is the established
    pattern per RESEARCH.
    """

    name = "alpaca"
    supports_fractional = True
    is_paper = True

    def __init__(self, *, api_key: str, secret_key: str, paper: bool = True) -> None:
        # ---- Layer 1: argument check (P1 paper-only invariant) -------------
        if not paper:
            # Knight-Capital insurance per Pitfall 7: live keys cannot reach
            # the TradingClient in Phase 1. Phase 2's OrderGuard adds the
            # promotion ladder; until then this is a hard physical rejection.
            msg = (
                "Phase 1 supports paper trading only (live blocked until "
                "Phase 2 OrderGuard). If you intended paper, pass paper=True."
            )
            raise BrokerConfigError(msg)

        # ---- Construct the underlying clients ------------------------------
        self._client: TradingClient = TradingClient(api_key, secret_key, paper=True)
        self._data_client: StockHistoricalDataClient = StockHistoricalDataClient(
            api_key, secret_key
        )

        # ---- Layer 2: post-construct probe ---------------------------------
        # alpaca-py 0.43 exposes ``_base_url`` as a ``BaseURL`` enum. We
        # accept either:
        #   * .value is a string like "https://paper-api.alpaca.markets/v2"
        #   * str(enum) is like "BaseURL.TRADING_PAPER"
        # Both contain "paper" (case-insensitive). A future alpaca-py
        # change that drops the substring will trip this check and force
        # a planned response rather than a silent live-order leak.
        base_url_value = getattr(self._client._base_url, "value", str(self._client._base_url))
        base_url_str = f"{self._client._base_url!s}|{base_url_value}".lower()
        if "paper" not in base_url_str:
            msg = (
                "Paper-mode assertion failed; refusing to construct broker. "
                f"TradingClient._base_url={self._client._base_url!r} does not "
                "look like a paper endpoint."
            )
            raise BrokerConfigError(msg)

    # -------------------------------------------------------------------
    # Brokerage ABC contract
    # -------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if we can fetch the account; False otherwise.

        MUST NOT raise (per ABC contract). Broad ``except`` is intentional —
        a health check that raises is broken-by-definition.
        """
        try:
            await self.get_account()
        except Exception:  # noqa: BLE001 - health checks swallow by contract
            return False
        return True

    @retry_on_rate_limit
    async def get_account(self) -> dict[str, Any]:
        """Return the Alpaca account state as a JSON-serializable dict.

        EXEC-08: wrapped with ``@retry_on_rate_limit`` — 429 rate-limit
        errors are retried with exponential backoff + jitter, up to 6
        total attempts. Non-429 errors propagate immediately.
        """
        acct = await asyncio.to_thread(self._client.get_account)
        return _model_dump(acct)

    @retry_on_rate_limit
    async def get_positions(self) -> list[dict[str, Any]]:
        """Return the list of open positions (empty list if none).

        EXEC-08: wrapped with ``@retry_on_rate_limit``.
        """
        positions = await asyncio.to_thread(self._client.get_all_positions)
        return [_model_dump(p) for p in positions]

    @retry_on_rate_limit
    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return the latest quote for ``symbol``.

        The dict contains the alpaca-py ``Quote`` shape (ask_price, bid_price,
        timestamp, etc.). Callers handling money MUST coerce the price
        fields via :func:`gekko.core.money.to_decimal` before any arithmetic
        — alpaca-py returns Decimal-shaped strings already, but treating
        them as opaque strings until the caller explicitly converts is the
        EXEC-01 belt-and-braces.
        """
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        result = await asyncio.to_thread(self._data_client.get_stock_latest_quote, req)
        quote = result[symbol]
        return _model_dump(quote)

    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Submit an order with Pitfall-4 duplicate handling.

        Builds the correct alpaca-py request type based on ``req.order_type``
        (LIMIT / MARKET / STOP — EXEC-07). All numeric inputs go in as
        ``str(Decimal)`` so the grep gate is satisfied and alpaca-py's
        Pydantic models receive lossless string values.

        If the broker raises a 422 / "duplicate" / "already exists" error,
        we route to :meth:`get_order_by_client_order_id` and return the
        existing order. The duplicate-rejection IS the safety net — we
        NEVER re-POST a submit on retry.
        """
        side = _SIDE_MAP[req.side]
        tif = _TIF_MAP[req.time_in_force]
        order_req = _build_order_request(req, side, tif)

        try:
            order = await asyncio.to_thread(self._client.submit_order, order_data=order_req)
        except APIError as e:
            # Pitfall 4 / Knight Capital prevention: duplicate client_order_id
            # is HTTP 422. We never re-POST — we look up the existing order.
            if _is_duplicate_error(e):
                existing = await self.get_order_by_client_order_id(req.client_order_id)
                if existing is not None:
                    return existing
                # If the lookup failed too, surface the error.
                msg = (
                    f"submit_order failed with duplicate-id 422, but "
                    f"get_order_by_client_order_id returned None for "
                    f"client_order_id={req.client_order_id!r}"
                )
                raise BrokerOrderError(msg) from e
            msg = f"submit_order failed: {e}"
            raise BrokerOrderError(msg) from e

        return _order_to_result(order, req.client_order_id)

    @retry_on_rate_limit
    async def get_order_by_client_order_id(self, client_order_id: str) -> OrderResult | None:
        """Look up an order by its deterministic client_order_id.

        Returns None on any lookup failure — this is a defensive probe (the
        Pitfall 4 escape hatch), so we never want it to raise upward and
        mask a 422-handling path. If the broker is genuinely down, the
        caller's place_order will surface that separately.

        EXEC-08: wrapped with ``@retry_on_rate_limit`` — 429s on the lookup
        path are retried with exponential backoff. Note: the outer
        ``try/except Exception: return None`` swallows the FINAL exception
        after retries are exhausted, so the probe still returns None on a
        sustained-429 broker.
        """
        try:
            order = await asyncio.to_thread(
                self._client.get_order_by_client_id, client_order_id
            )
        except Exception:  # noqa: BLE001 - probe by contract
            return None
        return _order_to_result(order, client_order_id)

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True on success.

        P1 keeps this minimal — rate-limit hardening and retry policy land
        in Phase 2's OrderGuard per SKELETON.md (§"What's Real vs Minimal").
        """
        await asyncio.to_thread(self._client.cancel_order_by_id, broker_order_id)
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_dump(obj: Any) -> dict[str, Any]:
    """Coerce an alpaca-py Pydantic model (or dict) into a JSON-friendly dict.

    Most alpaca-py 0.43 response types are Pydantic v2 models with
    ``.model_dump(mode="json")``; some older types (or our own mocks in
    tests) may be dicts already.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(obj, dict):
        return dict(obj)
    # Fallback: best-effort attribute scrape (used only by ad-hoc mocks).
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _build_order_request(req: OrderRequest, side: AlpacaOrderSide, tif: AlpacaTimeInForce) -> Any:
    """Build the alpaca-py request object for ``req.order_type``.

    Per EXEC-07: LIMIT, MARKET, STOP are the three supported types in
    Phase 1. Stop-limit and trailing-stop are explicitly out of scope.

    Quantity and prices are passed as ``str(Decimal)`` so the alpaca-py
    Pydantic model never sees a binary-fp builtin (EXEC-01 grep gate
    enforced by tests/unit/test_money_math.py).
    """
    qty_s = str(req.qty)
    common = {
        "symbol": req.symbol,
        "qty": qty_s,
        "side": side,
        "time_in_force": tif,
        "client_order_id": req.client_order_id,
    }
    if req.order_type is OrderType.LIMIT:
        if req.limit_price is None:
            msg = "LIMIT order requires limit_price"
            raise BrokerOrderError(msg)
        return LimitOrderRequest(limit_price=str(req.limit_price), **common)
    if req.order_type is OrderType.MARKET:
        return MarketOrderRequest(**common)
    if req.order_type is OrderType.STOP:
        if req.stop_price is None:
            msg = "STOP order requires stop_price"
            raise BrokerOrderError(msg)
        return StopOrderRequest(stop_price=str(req.stop_price), **common)
    msg = f"unsupported order_type: {req.order_type!r}"
    raise BrokerOrderError(msg)


def _is_duplicate_error(e: APIError) -> bool:
    """Detect Alpaca's duplicate-client_order_id response.

    The signal is HTTP 422 OR a body mentioning ``duplicate`` /
    ``already exists`` (alpaca-py 0.43 surfaces both in different code
    paths depending on whether the SDK pre-validated or the server did).
    """
    status_code = getattr(e, "status_code", None)
    if status_code == 422:
        return True
    text = str(e).lower()
    return "duplicate" in text or "already exists" in text or " 422" in text


def _order_to_result(order: Any, client_order_id: str) -> OrderResult:
    """Convert an alpaca-py Order model into our :class:`OrderResult`.

    All numeric fields are coerced via ``Decimal(str(...))`` — alpaca-py
    typically returns Decimal already, but the ``str()`` round-trip
    handles legacy paths that return numeric strings.
    """
    filled_qty_raw = getattr(order, "filled_qty", None)
    filled_qty = Decimal(str(filled_qty_raw)) if filled_qty_raw is not None else Decimal("0")

    filled_avg_raw = getattr(order, "filled_avg_price", None)
    filled_avg = Decimal(str(filled_avg_raw)) if filled_avg_raw is not None else None

    broker_order_id = str(getattr(order, "id", ""))
    status = str(getattr(order, "status", ""))
    return OrderResult(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status=status,
        filled_qty=filled_qty,
        avg_fill_price=filled_avg,
        raw=_model_dump(order),
    )


__all__: tuple[str, ...] = ("AlpacaBroker",)
