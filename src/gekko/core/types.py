"""Core enums for order-placement — Plan 01-05 Task 1.

Shared across ``gekko.brokers.*``, ``gekko.execution.*``, and the Pydantic
schemas in ``gekko.schemas.*``. Keeping these here (rather than inside
``gekko.brokers.base``) avoids a circular import the moment any non-broker
module needs to talk about an order side or order type.

Every enum is a ``str, Enum`` mixin so:

* ``OrderSide.BUY == "buy"`` is True (string equality works).
* ``json.dumps({"side": OrderSide.BUY})`` serializes to ``{"side": "buy"}``
  (no custom encoder needed; ``canonical_json`` falls through ``default=str``
  to the same value).
* Pydantic 2.x accepts both the enum member and its raw string value when
  parsing, which is what the agent-LLM tool-use bridge needs.

References:
  * CONTEXT.md D-20 — Decimal for money math
  * RESEARCH.md §"Code Examples — Brokerage ABC" — enum block
"""

from __future__ import annotations

from enum import StrEnum


class OrderSide(StrEnum):
    """The two sides of a market order. Mirrors ``alpaca.trading.enums.OrderSide``.

    Inherits from ``StrEnum`` so members behave as strings end-to-end
    (``OrderSide.BUY == "buy"`` is True; ``json.dumps({"side": OrderSide.BUY})``
    emits ``{"side": "buy"}``).
    """

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Order-placement type. P1 supports market, limit, stop (EXEC-07).

    More exotic types (``TRAILING_STOP``, ``BRACKET``, ``OCO``) are NOT
    supported in P1 — adding them is a one-line enum extension plus a
    corresponding request-builder branch in ``AlpacaBroker.place_order``.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(StrEnum):
    """Order time-in-force. P1 supports ``DAY`` (cancel at session close)
    and ``GTC`` (good-til-canceled). Mirrors ``alpaca.trading.enums.TimeInForce``."""

    DAY = "day"
    GTC = "gtc"


__all__: tuple[str, ...] = ("OrderSide", "OrderType", "TimeInForce")
