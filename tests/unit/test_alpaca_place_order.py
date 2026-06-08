"""``AlpacaBroker.place_order`` — Plan 01-05 Task 3.

Three concerns:

1. **EXEC-07 order-type coverage** — LIMIT, MARKET, STOP all map to the
   correct alpaca-py request type and round-trip through ``submit_order``.

2. **Pitfall 4 / Knight Capital prevention** — when ``submit_order`` raises
   an ``APIError`` with status_code 422 (or "duplicate" / "already exists"
   in the body), ``place_order`` MUST:
     * call ``get_order_by_client_order_id(req.client_order_id)``,
     * return the existing order's ``OrderResult``,
     * NEVER re-POST the submit.

3. **str(Decimal) handoff** — every numeric input to alpaca-py crosses the
   boundary as ``str(Decimal)``, never as the binary-fp builtin. (The
   ``test_money_math::test_float_banned_in_money_paths`` grep gate enforces
   this statically; the tests here verify the runtime behavior the gate
   protects.)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from alpaca.common.exceptions import APIError


def _build_paper_broker(mocker: Any) -> tuple[Any, MagicMock]:
    """Construct an ``AlpacaBroker`` with TradingClient + DataClient mocked.

    Returns ``(broker, tc_mock)`` so the test can inject responses on the
    TradingClient mock for the specific scenario under test.
    """
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://paper-api.alpaca.markets/v2"
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="paper-acct-abc")

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    broker = AlpacaBroker(api_key="x", secret_key="y", paper=True)
    return broker, tc_mock


def _fake_order(
    order_id: str = "broker-abc",
    client_order_id: str = "cid-abc",
    status: str = "accepted",
    filled_qty: str = "0",
    filled_avg_price: str | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like an alpaca-py Order model."""
    mock = MagicMock()
    mock.id = order_id
    mock.client_order_id = client_order_id
    mock.status = status
    mock.filled_qty = filled_qty
    mock.filled_avg_price = filled_avg_price
    mock.model_dump = MagicMock(return_value={
        "id": order_id,
        "client_order_id": client_order_id,
        "status": status,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
    })
    return mock


# ---------------------------------------------------------------------------
# Order-type coverage (EXEC-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_order_routes_to_LimitOrderRequest(mocker: Any) -> None:
    from gekko.brokers.base import OrderRequest
    from gekko.core.types import OrderSide, OrderType, TimeInForce

    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.submit_order.return_value = _fake_order()

    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1234.56"),
        time_in_force=TimeInForce.DAY,
        client_order_id="cid-1",
    )
    result = await broker.place_order(req)

    # submit_order called once with a LimitOrderRequest-shaped object
    tc_mock.return_value.submit_order.assert_called_once()
    call_kwargs = tc_mock.return_value.submit_order.call_args.kwargs
    order_data = call_kwargs["order_data"]
    assert type(order_data).__name__ == "LimitOrderRequest"
    # qty crossed as str at our boundary; alpaca-py's Pydantic model
    # may coerce internally — what we verify is the value, not the type.
    assert str(order_data.qty) == "5" or order_data.qty == 5
    assert str(order_data.limit_price) == "1234.56" or order_data.limit_price == Decimal("1234.56")
    assert result.broker_order_id == "broker-abc"


@pytest.mark.asyncio
async def test_market_order_routes_to_MarketOrderRequest(mocker: Any) -> None:
    from gekko.brokers.base import OrderRequest
    from gekko.core.types import OrderSide, OrderType

    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.submit_order.return_value = _fake_order()

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("10"),
        order_type=OrderType.MARKET,
        client_order_id="cid-m",
    )
    await broker.place_order(req)

    order_data = tc_mock.return_value.submit_order.call_args.kwargs["order_data"]
    assert type(order_data).__name__ == "MarketOrderRequest"
    assert str(order_data.qty) == "10" or order_data.qty == 10


@pytest.mark.asyncio
async def test_stop_order_routes_to_StopOrderRequest(mocker: Any) -> None:
    from gekko.brokers.base import OrderRequest
    from gekko.core.types import OrderSide, OrderType

    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.submit_order.return_value = _fake_order()

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("2"),
        order_type=OrderType.STOP,
        stop_price=Decimal("180.50"),
        client_order_id="cid-s",
    )
    await broker.place_order(req)

    order_data = tc_mock.return_value.submit_order.call_args.kwargs["order_data"]
    assert type(order_data).__name__ == "StopOrderRequest"
    assert str(order_data.stop_price) == "180.50" or order_data.stop_price == Decimal("180.50")


@pytest.mark.asyncio
async def test_limit_order_without_limit_price_raises(mocker: Any) -> None:
    from gekko.brokers.base import OrderRequest
    from gekko.core.errors import BrokerOrderError
    from gekko.core.types import OrderSide, OrderType

    broker, _ = _build_paper_broker(mocker)
    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.LIMIT,
        client_order_id="cid-bad",
    )
    with pytest.raises(BrokerOrderError):
        await broker.place_order(req)


@pytest.mark.asyncio
async def test_stop_order_without_stop_price_raises(mocker: Any) -> None:
    from gekko.brokers.base import OrderRequest
    from gekko.core.errors import BrokerOrderError
    from gekko.core.types import OrderSide, OrderType

    broker, _ = _build_paper_broker(mocker)
    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.SELL,
        qty=Decimal("5"),
        order_type=OrderType.STOP,
        client_order_id="cid-bad",
    )
    with pytest.raises(BrokerOrderError):
        await broker.place_order(req)


# ---------------------------------------------------------------------------
# Pitfall 4 / Knight Capital — duplicate 422 handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_422_duplicate_calls_get_order_and_returns_existing(mocker: Any) -> None:
    """The critical Knight Capital test.

    When ``submit_order`` raises APIError(status_code=422), ``place_order``
    MUST query ``get_order_by_client_id`` and return that existing order.
    The submit_order POST is called exactly once (NEVER retried).
    """
    from gekko.brokers.base import OrderRequest
    from gekko.core.types import OrderSide, OrderType

    broker, tc_mock = _build_paper_broker(mocker)

    # First submit_order call raises 422
    api_err = APIError("duplicate client_order_id", http_error=None)
    # alpaca-py's APIError tries to JSON-decode the message; status_code is
    # set via response/HTTPError. We patch directly:
    type(api_err).status_code = property(lambda self: 422)  # type: ignore[misc]
    tc_mock.return_value.submit_order.side_effect = api_err

    # get_order_by_client_id returns the existing order
    existing_order = _fake_order(
        order_id="broker-existing",
        client_order_id="cid-dup",
        status="accepted",
        filled_qty="0",
    )
    tc_mock.return_value.get_order_by_client_id.return_value = existing_order

    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.MARKET,
        client_order_id="cid-dup",
    )
    result = await broker.place_order(req)

    # The result wraps the EXISTING order, not a re-POSTed one.
    assert result.broker_order_id == "broker-existing"
    assert result.client_order_id == "cid-dup"

    # submit_order called exactly once (NEVER retried).
    assert tc_mock.return_value.submit_order.call_count == 1
    # get_order_by_client_id called with the right client_order_id.
    tc_mock.return_value.get_order_by_client_id.assert_called_once_with("cid-dup")


@pytest.mark.asyncio
async def test_422_duplicate_with_no_existing_order_raises(mocker: Any) -> None:
    """If the duplicate-id lookup returns None, surface a BrokerOrderError.

    Edge case: the broker said 422 (duplicate) but we can't find the
    existing order — something is genuinely wrong. We refuse to silently
    swallow this; the caller sees a typed error.
    """
    from gekko.brokers.base import OrderRequest
    from gekko.core.errors import BrokerOrderError
    from gekko.core.types import OrderSide, OrderType

    broker, tc_mock = _build_paper_broker(mocker)

    api_err = APIError("duplicate client_order_id", http_error=None)
    type(api_err).status_code = property(lambda self: 422)  # type: ignore[misc]
    tc_mock.return_value.submit_order.side_effect = api_err

    # get_order_by_client_id raises -> probe returns None per the broad except
    tc_mock.return_value.get_order_by_client_id.side_effect = RuntimeError("network down")

    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.MARKET,
        client_order_id="cid-orphan",
    )
    with pytest.raises(BrokerOrderError):
        await broker.place_order(req)

    # submit_order still called exactly once — no retry.
    assert tc_mock.return_value.submit_order.call_count == 1


@pytest.mark.asyncio
async def test_non_422_error_propagates_as_BrokerOrderError(mocker: Any) -> None:
    """A non-duplicate APIError surfaces as BrokerOrderError.

    Generic broker failures (5xx, network errors with non-422 status) are
    not the Pitfall-4 case. We wrap them in BrokerOrderError so callers
    have a typed error contract.
    """
    from gekko.brokers.base import OrderRequest
    from gekko.core.errors import BrokerOrderError
    from gekko.core.types import OrderSide, OrderType

    broker, tc_mock = _build_paper_broker(mocker)

    api_err = APIError("server unavailable", http_error=None)
    type(api_err).status_code = property(lambda self: 503)  # type: ignore[misc]
    tc_mock.return_value.submit_order.side_effect = api_err

    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.MARKET,
        client_order_id="cid-fail",
    )
    with pytest.raises(BrokerOrderError):
        await broker.place_order(req)

    # get_order_by_client_id NOT called for non-422 errors.
    tc_mock.return_value.get_order_by_client_id.assert_not_called()


# ---------------------------------------------------------------------------
# cancel_order, get_account, get_positions — basic wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order_delegates_to_client(mocker: Any) -> None:
    broker, tc_mock = _build_paper_broker(mocker)
    ok = await broker.cancel_order("broker-abc")
    assert ok is True
    tc_mock.return_value.cancel_order_by_id.assert_called_once_with("broker-abc")


@pytest.mark.asyncio
async def test_get_order_by_client_order_id_returns_None_on_error(mocker: Any) -> None:
    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.get_order_by_client_id.side_effect = RuntimeError("not found")
    result = await broker.get_order_by_client_order_id("missing-cid")
    assert result is None


@pytest.mark.asyncio
async def test_health_check_returns_false_on_error(mocker: Any) -> None:
    """Health check MUST never raise (per ABC contract)."""
    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.get_account.side_effect = RuntimeError("broker down")
    ok = await broker.health_check()
    assert ok is False


@pytest.mark.asyncio
async def test_health_check_returns_true_on_success(mocker: Any) -> None:
    broker, tc_mock = _build_paper_broker(mocker)
    tc_mock.return_value.get_account.return_value = mocker.Mock(
        id="paper-acct-abc",
        model_dump=mocker.Mock(return_value={"id": "paper-acct-abc"}),
    )
    ok = await broker.health_check()
    assert ok is True
