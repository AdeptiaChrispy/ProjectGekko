"""``AlpacaFillStream`` — Plan 01-05 Task 3.

Tests the TradingStream wrapper's fill-routing logic. We don't actually
start the worker thread (that would open a real websocket); instead we
construct the stream, capture the registered callback, and feed it fake
trade-update payloads directly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_fill_event_routes_to_callback(mocker: Any) -> None:
    """A ``fill`` trade-update routes to ``on_fill`` with the expected payload."""
    from gekko.brokers.stream import AlpacaFillStream

    mocker.patch("gekko.brokers.stream.TradingStream")

    captured: list[dict[str, Any]] = []

    async def on_fill(payload: dict[str, Any]) -> None:
        captured.append(payload)

    stream = AlpacaFillStream(
        api_key="x",
        secret_key="y",
        user_id="user-1",
        on_fill=on_fill,
    )

    # Build a fake TradeUpdate shape and feed it to the registered callback.
    order = MagicMock()
    order.id = "broker-xyz"
    order.client_order_id = "cid-1"
    order.filled_qty = "5"
    order.filled_avg_price = "100.25"
    order.symbol = "NVDA"

    data = MagicMock()
    data.event = "fill"
    data.order = order

    await stream._handle_trade_update(data)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["client_order_id"] == "cid-1"
    assert payload["broker_order_id"] == "broker-xyz"
    assert payload["filled_qty"] == "5"
    assert payload["filled_avg_price"] == "100.25"
    assert payload["ticker"] == "NVDA"
    assert payload["user_id"] == "user-1"
    assert payload["event"] == "fill"


@pytest.mark.asyncio
async def test_partial_fill_event_routes_to_callback(mocker: Any) -> None:
    """``partial_fill`` events are also routed (P1 needs both)."""
    from gekko.brokers.stream import AlpacaFillStream

    mocker.patch("gekko.brokers.stream.TradingStream")

    captured: list[dict[str, Any]] = []

    async def on_fill(payload: dict[str, Any]) -> None:
        captured.append(payload)

    stream = AlpacaFillStream(
        api_key="x", secret_key="y", user_id="user-1", on_fill=on_fill
    )

    order = MagicMock()
    order.id = "broker-xyz"
    order.client_order_id = "cid-2"
    order.filled_qty = "3"
    order.filled_avg_price = "100.25"
    order.symbol = "NVDA"

    data = MagicMock()
    data.event = "partial_fill"
    data.order = order

    await stream._handle_trade_update(data)

    assert len(captured) == 1
    assert captured[0]["event"] == "partial_fill"
    assert captured[0]["filled_qty"] == "3"


@pytest.mark.asyncio
async def test_non_fill_events_ignored(mocker: Any) -> None:
    """``new``, ``accepted``, ``canceled`` etc. do NOT invoke ``on_fill``."""
    from gekko.brokers.stream import AlpacaFillStream

    mocker.patch("gekko.brokers.stream.TradingStream")

    captured: list[dict[str, Any]] = []

    async def on_fill(payload: dict[str, Any]) -> None:
        captured.append(payload)

    stream = AlpacaFillStream(
        api_key="x", secret_key="y", user_id="user-1", on_fill=on_fill
    )

    for event_name in ("new", "accepted", "canceled", "rejected", "expired", "replaced"):
        data = MagicMock()
        data.event = event_name
        data.order = MagicMock()
        await stream._handle_trade_update(data)

    assert captured == []


@pytest.mark.asyncio
async def test_subscribe_called_at_construction(mocker: Any) -> None:
    """``subscribe_trade_updates`` is invoked exactly once during __init__."""
    from gekko.brokers.stream import AlpacaFillStream

    ts_mock = mocker.patch("gekko.brokers.stream.TradingStream")

    async def on_fill(_payload: dict[str, Any]) -> None:
        pass

    AlpacaFillStream(api_key="x", secret_key="y", user_id="user-1", on_fill=on_fill)

    ts_mock.assert_called_once_with("x", "y", paper=True)
    ts_mock.return_value.subscribe_trade_updates.assert_called_once()


@pytest.mark.asyncio
async def test_stop_without_start_is_safe(mocker: Any) -> None:
    """Calling ``stop()`` before ``start()`` is a no-op."""
    from gekko.brokers.stream import AlpacaFillStream

    mocker.patch("gekko.brokers.stream.TradingStream")

    async def on_fill(_payload: dict[str, Any]) -> None:
        pass

    stream = AlpacaFillStream(
        api_key="x", secret_key="y", user_id="user-1", on_fill=on_fill
    )
    # Should not raise.
    await stream.stop()
