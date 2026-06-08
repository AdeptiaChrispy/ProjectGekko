"""``AlpacaFillStream`` — TradingStream wrapper — Plan 01-05 Task 3 / 4.

Wraps ``alpaca.trading.stream.TradingStream`` so the application loop can
listen for fill / partial-fill events via a single async callback. The
broker-specific stream object stays inside this module; everything outside
talks to the abstract ``on_fill(payload: dict) -> None`` callback signature.

Lifecycle:

* ``__init__`` builds the TradingStream and registers
  ``_handle_trade_update`` as the subscription callback.
* ``start()`` schedules ``self._stream.run()`` inside an ``asyncio.to_thread``
  task and returns the Task so the caller can await it (or store it).
  Reason: alpaca-py 0.43's ``TradingStream.run()`` is a blocking sync loop
  that internally drives a websocket client; the cleanest async integration
  is to push the entire blocking loop into a worker thread.
* ``stop()`` calls ``self._stream.stop()`` (signals the run loop to exit),
  cancels the worker task, and waits for it.

Payload shape passed to ``on_fill``::

    {
        "client_order_id": str,
        "broker_order_id": str,
        "filled_qty": str,          # Decimal-shaped string
        "filled_avg_price": str,    # Decimal-shaped string
        "ticker": str,
        "user_id": str,
    }

Strings (not Decimals) are used in the payload so the dict round-trips
through canonical JSON unchanged — Decimal serialization is a Pitfall 6
concern callers must explicitly opt into via
``gekko.audit.canonical.normalize_decimals``.

References:
  * RESEARCH.md §"TradingStream fill listener"
  * VALIDATION.md BROK-A-06 — websocket fill listener
  * D-15 — full structured rationale in audit payload
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from alpaca.trading.stream import TradingStream

FillCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AlpacaFillStream:
    """Subscribe to Alpaca trade_updates and route fill events to ``on_fill``.

    Phase 1 only consumes ``fill`` and ``partial_fill`` events. Other event
    types (``new``, ``accepted``, ``canceled``, ``rejected``, ``expired``)
    are ignored at this layer — they are observed via the order lifecycle
    in the executor (Plan 01-08) rather than the websocket stream.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        user_id: str,
        on_fill: FillCallback,
    ) -> None:
        # paper=True is hard-coded — the TradingStream MUST point at the
        # paper endpoint in Phase 1. Live-stream support is a Phase 2 concern.
        self._stream = TradingStream(api_key, secret_key, paper=True)
        self._user_id = user_id
        self._on_fill = on_fill
        self._task: asyncio.Task[Any] | None = None

        # subscribe at construction time so callers don't have to remember
        # an extra step before start().
        self._stream.subscribe_trade_updates(self._handle_trade_update)

    async def _handle_trade_update(self, data: Any) -> None:
        """Trade-update callback registered with the TradingStream.

        ``data`` is the alpaca-py ``TradeUpdate`` model. ``data.event`` is
        a string ('fill', 'partial_fill', 'new', etc.); ``data.order`` is
        the Order model with the standard fields.
        """
        event = getattr(data, "event", None)
        if event not in ("fill", "partial_fill"):
            return

        order = data.order
        payload: dict[str, Any] = {
            "client_order_id": getattr(order, "client_order_id", ""),
            "broker_order_id": str(getattr(order, "id", "")),
            "filled_qty": str(getattr(order, "filled_qty", "0") or "0"),
            "filled_avg_price": str(getattr(order, "filled_avg_price", "") or ""),
            "ticker": getattr(order, "symbol", ""),
            "user_id": self._user_id,
            "event": event,
        }
        await self._on_fill(payload)

    def start(self) -> asyncio.Task[Any]:
        """Start the underlying TradingStream in a worker thread.

        Returns the ``asyncio.Task`` wrapping the blocking ``stream.run()``.
        The caller owns the task — typical pattern is to store it on the
        broker / executor and ``await self.stop()`` on shutdown.
        """
        if self._task is not None:
            return self._task
        # ``self._stream.run`` is a blocking sync loop. Pushing it into
        # ``to_thread`` keeps the main event loop responsive while the
        # websocket reader runs in a worker thread.
        self._task = asyncio.create_task(asyncio.to_thread(self._stream.run))
        return self._task

    async def stop(self) -> None:
        """Stop the TradingStream and tear down the worker task."""
        if self._task is None:
            return
        # Signal the run loop to exit; alpaca-py 0.43's TradingStream.stop()
        # closes the websocket and returns. The worker thread should then
        # exit cleanly. Tolerate stop() raising — it's cleanup-best-effort.
        with contextlib.suppress(Exception):
            self._stream.stop()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None


__all__: tuple[str, ...] = ("AlpacaFillStream", "FillCallback")
