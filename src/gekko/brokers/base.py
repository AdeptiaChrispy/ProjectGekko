"""``Brokerage`` ABC — the load-bearing broker interface — Plan 01-05 Task 2.

Per RESEARCH §"Architecture Patterns": this is the **load-bearing interface**
every later phase plugs into. Plans that extend it:

* Phase 2 hook: P2 OrderGuard wraps :meth:`Brokerage.place_order` with the
  universe-whitelist + hard-cap + paper/live env-pairing checks before
  delegating here. See ROADMAP.md Phase 2 success criteria. The OrderGuard
  has the same ``async def place_order(req: OrderRequest) -> OrderResult``
  signature and decorates whatever concrete broker the user has configured.

* Phase 8 extension: ``IBKRBroker`` (via ``ib_async`` + TWS / IB Gateway
  side-process) and ``SchwabBroker`` (``schwab-py`` + 7-day refresh-token
  coordinator) implement this same ABC. The async signatures are the
  contract — IBKR is natively sync (``ib_async`` wraps it) and Schwab's
  OAuth refresh is the new operational concern, but neither changes the
  ABC.

* Phase 9 extension: ``RobinhoodBrowserBroker`` and ``FidelityBrowserBroker``
  use ``browser-use``; same ABC contract, plus an additional
  ``capture_screenshot()`` audit hook per BROK-R-05. The browser-fallback
  brokers are deliberately a subtype of ``Brokerage`` — their place_order
  paths return an ``OrderResult`` with the same shape, just with screenshot
  paths attached to ``raw``.

The seven abstract methods (per RESEARCH list):

* ``health_check`` — cheap "can I talk to the broker?" probe; returns bool.
* ``get_account`` — account state (buying power, status, etc.).
* ``get_positions`` — open positions list.
* ``get_quote(symbol)`` — latest quote shape (ask/bid/timestamp).
* ``place_order(req)`` — submit an order and return the result.
* ``get_order_by_client_order_id(cid)`` — duplicate-detection probe used by
  the place_order 422 handler (Pitfall 4 / Knight Capital).
* ``cancel_order(broker_order_id)`` — cancel an open order.

References:
  * CONTEXT.md D-20 — Decimal everywhere
  * CONTEXT.md D-21 — per-user isolated deployment
  * CONTEXT.md D-24 — alpaca-py paper-only in P1
  * SKELETON.md §What's Real vs Minimal — Broker: paper only; live blocked
  * RESEARCH.md §Code Examples — Brokerage ABC
  * RESEARCH.md §Pitfall 4 — Knight Capital duplicate-order prevention
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from gekko.core.types import OrderSide, OrderType, TimeInForce

# ---------------------------------------------------------------------------
# Data carriers — frozen dataclasses for ergonomic, immutable order shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderRequest:
    """A broker-agnostic order submission shape.

    The agent runtime (Plan 01-07) builds this from a ``TradeProposal``;
    the executor (Plan 01-08) hands it to ``Brokerage.place_order``. Frozen
    so it can be safely captured in audit log payloads and passed across
    async boundaries without aliasing concerns.

    The ``client_order_id`` field is the deterministic idempotency key from
    :func:`gekko.core.ids.compute_client_order_id` — the same OrderRequest
    constructed twice for the same research cycle will carry the same id,
    which the broker uses to reject duplicate POSTs (Pitfall 4).

    All money fields are ``Decimal``. The grep gate
    (``tests/unit/test_money_math.py::test_float_banned_in_money_paths``)
    forbids the binary-fp builtin anywhere in this file.
    """

    symbol: str
    side: OrderSide
    qty: Decimal
    order_type: OrderType
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str = ""


@dataclass(frozen=True)
class OrderResult:
    """The shape ``Brokerage.place_order`` returns.

    ``broker_order_id`` is the broker-side primary key (used for cancel /
    query); ``client_order_id`` is the deterministic id supplied in the
    request. ``status`` is the broker's lifecycle state at the moment of
    submission (``accepted``, ``new``, ``filled``, ``partially_filled``,
    ``rejected``, etc.) — order completion is observed via the fill
    stream (Task 4), not via place_order's return value.

    ``raw`` holds the full broker response dict for the audit log payload
    (D-15 structured rationale requirement).
    """

    broker_order_id: str
    client_order_id: str
    status: str
    filled_qty: Decimal
    avg_fill_price: Decimal | None
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class Brokerage(ABC):
    """Abstract base class every concrete broker MUST implement.

    Concrete subclasses set the class attributes ``name``,
    ``supports_fractional``, and ``is_paper`` so callers can introspect
    capabilities without method calls (e.g., the agent's "can I fractional-
    order this position?" check).

    Phase 2 hook: P2 OrderGuard wraps :meth:`place_order` with universe-
    whitelist + hard-cap + paper/live env-pairing checks before delegating
    here. See ROADMAP.md Phase 2 success criteria.

    Phase 8 extension: IBKRBroker (via ib_async + TWS/IB Gateway side-
    process) and SchwabBroker (schwab-py + 7-day refresh-token coordinator)
    implement this same ABC.

    Phase 9 extension: RobinhoodBrowserBroker and FidelityBrowserBroker use
    browser-use; same ABC contract, additional ``capture_screenshot()``
    audit hook per BROK-R-05.
    """

    # Class attributes subclasses are expected to set.
    name: str = "abstract"
    supports_fractional: bool = False
    is_paper: bool = False

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the broker can be reached, False otherwise.

        Should NEVER raise — health checks that fail by exception break the
        retry / supervised-restart logic in Plan 01-09 / Phase 7.
        """

    @abstractmethod
    async def get_account(self) -> dict[str, Any]:
        """Return the account state shape (id, buying_power, status, etc.)."""

    @abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]:
        """Return the list of open positions (empty list if none)."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return the latest quote dict for ``symbol`` (ask, bid, timestamp)."""

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Submit an order.

        Implementations MUST handle the duplicate-id case (Pitfall 4): if
        the broker returns HTTP 422 / "already exists" / "duplicate" on a
        POST, the implementation calls :meth:`get_order_by_client_order_id`
        and returns the existing order's OrderResult. NEVER re-POSTs.
        """

    @abstractmethod
    async def get_order_by_client_order_id(self, client_order_id: str) -> OrderResult | None:
        """Look up an order by its deterministic client_order_id.

        Returns None if no such order exists (caller is then free to POST).
        Used as the Pitfall 4 duplicate-handling escape hatch by
        :meth:`place_order`.
        """

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order by broker_order_id. Returns True on success."""

    @abstractmethod
    async def get_orders_open(self) -> list[dict[str, Any]]:
        """Return open orders for this account. P2 kill switch uses this.

        Phase-2 addition (plan 02-05) for the kill switch (EXEC-06 / D-37).
        Returns each open order as a JSON-friendly dict (alpaca-py's
        ``Order.model_dump(mode='json')`` shape — id, symbol, side, qty,
        order_type, status, etc.).
        """

    @abstractmethod
    async def cancel_all_open_orders(self) -> list[dict[str, Any]]:
        """Cancel ALL open orders for this account. Returns the broker's per-order status list.

        Phase-2 addition (plan 02-05) for the kill switch (EXEC-06 / D-37).
        Concrete brokers SHOULD use a single-HTTP-call batch cancel where
        the underlying SDK supports it (alpaca-py ``TradingClient.cancel_orders()``)
        rather than iterating per-order.

        Per RESEARCH §6 Open Question #1 (verbatim): this method MUST NOT
        be decorated with ``@retry_on_rate_limit`` — a 429 retry storm
        during a kill is the worst possible failure mode; the kill switch
        owns failure-tolerance via ``asyncio.gather`` + 4s timeout.
        """


__all__: tuple[str, ...] = (
    "Brokerage",
    "OrderRequest",
    "OrderResult",
    # Re-export the enums so callers can do
    # ``from gekko.brokers.base import OrderSide`` if they prefer the
    # broker-layer namespace.
    "OrderSide",
    "OrderType",
    "TimeInForce",
)
