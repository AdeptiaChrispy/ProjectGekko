"""Alpaca paper round-trip — Plan 01-05 Task 4.

Closes BROK-A-01 (paper connect), BROK-A-03 (account + positions),
BROK-A-04 (place with client_order_id), BROK-A-05 (cancel), and BROK-A-06
(websocket fill) — see VALIDATION.md.

**Two execution modes** (per the plan):

* **Cassette mode (default).** Runs in CI without credentials. The test
  patches ``gekko.brokers.alpaca.TradingClient`` and
  ``gekko.brokers.alpaca.StockHistoricalDataClient`` to return canned
  responses loaded from
  ``tests/fixtures/cassettes/alpaca_paper_round_trip.json``. The fill-
  stream test feeds the recorded fill payload directly through the
  stream's ``_handle_trade_update`` callback (no real websocket).

* **Live paper mode.** Activated by ``GEKKO_TEST_LIVE_ALPACA=1`` plus
  real ``ALPACA_PAPER_API_KEY`` / ``ALPACA_PAPER_SECRET_KEY`` env vars.
  Talks to the real Alpaca paper endpoint. The fill-stream test
  additionally requires market hours (checked inline via
  ``pandas_market_calendars`` — see VALIDATION §Manual-Only).

Tests are marked ``@pytest.mark.integration`` so the fast unit feedback
loop is unaffected.

Why ``unittest.mock`` instead of ``respx``: alpaca-py 0.43 uses
``requests`` under the hood (not httpx) for the TradingClient REST
calls. respx hooks httpx; mocking at the TradingClient method boundary
is closer to the broker code's contract and more robust to alpaca-py
internal HTTP-stack changes.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level config + fixtures
# ---------------------------------------------------------------------------

LIVE_MODE = os.environ.get("GEKKO_TEST_LIVE_ALPACA") == "1"
HAVE_PAPER_CREDS = bool(
    os.environ.get("ALPACA_PAPER_API_KEY") and os.environ.get("ALPACA_PAPER_SECRET_KEY")
)


CASSETTE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cassettes" / "alpaca_paper_round_trip.json"
)


def _load_cassette() -> dict[str, Any]:
    with CASSETTE_PATH.open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _make_order_mock(order_dict: dict[str, Any], client_order_id: str) -> MagicMock:
    """Build a MagicMock that quacks like an alpaca-py Order model.

    ``client_order_id`` is injected at runtime — the cassette holds a
    placeholder so tests can use a fresh deterministic id per run.
    """
    mock = MagicMock()
    mock.id = order_dict["id"]
    mock.client_order_id = client_order_id
    mock.symbol = order_dict["symbol"]
    mock.qty = order_dict.get("qty", "0")
    mock.status = order_dict["status"]
    mock.filled_qty = order_dict.get("filled_qty", "0")
    mock.filled_avg_price = order_dict.get("filled_avg_price")
    # Make model_dump return the dict (with client_order_id swapped in)
    dumped = dict(order_dict)
    dumped["client_order_id"] = client_order_id
    mock.model_dump = MagicMock(return_value=dumped)
    return mock


@pytest.fixture
def cassette() -> dict[str, Any]:
    return _load_cassette()


@pytest.fixture
def alpaca_broker_cassette(mocker: Any, cassette: dict[str, Any]) -> Any:
    """An ``AlpacaBroker`` instance with TradingClient + DataClient mocked
    against the cassette responses.

    Used in cassette mode only. Live mode tests construct a real
    AlpacaBroker via env credentials.
    """
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://paper-api.alpaca.markets/v2"

    # get_account: the constructor probe AND the test both call this.
    account_mock = MagicMock()
    account_mock.id = cassette["account"]["id"]
    account_mock.model_dump = MagicMock(return_value=cassette["account"])
    tc_mock.return_value.get_account.return_value = account_mock

    # get_all_positions
    positions = []
    for p_dict in cassette["positions"]:
        p = MagicMock()
        p.model_dump = MagicMock(return_value=p_dict)
        positions.append(p)
    tc_mock.return_value.get_all_positions.return_value = positions

    # cancel_order_by_id
    tc_mock.return_value.cancel_order_by_id.return_value = cassette["cancel_response"]

    # data client
    dc_mock = mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")
    quote = MagicMock()
    quote.model_dump = MagicMock(return_value=cassette["quote_AAPL"])
    dc_mock.return_value.get_stock_latest_quote.return_value = {"AAPL": quote}

    broker = AlpacaBroker(api_key="cassette-key", secret_key="cassette-secret", paper=True)
    # Re-tag the broker with the underlying mocks so tests can inject
    # per-test responses (e.g., for submit_order).
    broker._tc_mock = tc_mock  # type: ignore[attr-defined]
    broker._dc_mock = dc_mock  # type: ignore[attr-defined]
    return broker


@pytest.fixture
def alpaca_broker_live() -> Any:
    """Real ``AlpacaBroker`` from env credentials (live paper mode only)."""
    if not LIVE_MODE or not HAVE_PAPER_CREDS:
        pytest.skip("Live paper mode requires GEKKO_TEST_LIVE_ALPACA=1 + ALPACA_PAPER_* env")
    from gekko.brokers.alpaca import AlpacaBroker

    return AlpacaBroker(
        api_key=os.environ["ALPACA_PAPER_API_KEY"],
        secret_key=os.environ["ALPACA_PAPER_SECRET_KEY"],
        paper=True,
    )


# ---------------------------------------------------------------------------
# BROK-A-01: paper connect + health_check
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_health_check_cassette(alpaca_broker_cassette: Any) -> None:
    """Health check succeeds against the cassette account."""
    ok = await alpaca_broker_cassette.health_check()
    assert ok is True


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_MODE, reason="GEKKO_TEST_LIVE_ALPACA not set")
async def test_alpaca_paper_health_check_live(alpaca_broker_live: Any) -> None:
    ok = await alpaca_broker_live.health_check()
    assert ok is True


# ---------------------------------------------------------------------------
# BROK-A-03: account + positions
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_get_account_cassette(alpaca_broker_cassette: Any) -> None:
    account = await alpaca_broker_cassette.get_account()
    assert "id" in account
    assert account["id"].startswith("paper-")
    assert "buying_power" in account
    assert "status" in account


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_get_positions_cassette(alpaca_broker_cassette: Any) -> None:
    positions = await alpaca_broker_cassette.get_positions()
    assert isinstance(positions, list)  # may be empty


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_MODE, reason="GEKKO_TEST_LIVE_ALPACA not set")
async def test_alpaca_paper_get_account_live(alpaca_broker_live: Any) -> None:
    account = await alpaca_broker_live.get_account()
    assert "id" in account
    assert "buying_power" in account
    assert "status" in account


# ---------------------------------------------------------------------------
# BROK-A-04: place limit order + retrieve by client_order_id
# BROK-A-05: cancel
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_place_limit_order_and_cancel_cassette(
    alpaca_broker_cassette: Any, cassette: dict[str, Any]
) -> None:
    """Place LIMIT order, retrieve by client_order_id, cancel — full round trip."""
    from gekko.brokers.base import OrderRequest
    from gekko.core.ids import compute_client_order_id
    from gekko.core.types import OrderSide, OrderType, TimeInForce

    cid = compute_client_order_id(
        strategy_id="test",
        decision_id=str(uuid.uuid4()),
        side="buy",
        qty=Decimal("1"),
        ticker="AAPL",
    )

    # Pin submit_order + get_order_by_client_id to return the cassette order
    # with the runtime client_order_id substituted in.
    order_mock = _make_order_mock(cassette["limit_order_AAPL"], cid)
    alpaca_broker_cassette._tc_mock.return_value.submit_order.return_value = order_mock
    alpaca_broker_cassette._tc_mock.return_value.get_order_by_client_id.return_value = order_mock

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("180.45"),
        time_in_force=TimeInForce.DAY,
        client_order_id=cid,
    )
    result = await alpaca_broker_cassette.place_order(req)
    assert result.broker_order_id == "broker-order-LIMIT-CASSETTE"
    assert result.client_order_id == cid

    # Retrieve by client_order_id
    fetched = await alpaca_broker_cassette.get_order_by_client_order_id(cid)
    assert fetched is not None
    assert fetched.broker_order_id == "broker-order-LIMIT-CASSETTE"

    # Cancel
    cancel_ok = await alpaca_broker_cassette.cancel_order(result.broker_order_id)
    assert cancel_ok is True
    alpaca_broker_cassette._tc_mock.return_value.cancel_order_by_id.assert_called_once_with(
        result.broker_order_id
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_duplicate_client_order_id_returns_existing_cassette(
    alpaca_broker_cassette: Any, cassette: dict[str, Any]
) -> None:
    """Knight Capital test in cassette mode.

    Simulate a 422 from submit_order and verify ``place_order`` returns
    the existing order via ``get_order_by_client_id`` — never re-POSTs.
    """
    from alpaca.common.exceptions import APIError

    from gekko.brokers.base import OrderRequest
    from gekko.core.ids import compute_client_order_id
    from gekko.core.types import OrderSide, OrderType, TimeInForce

    cid = compute_client_order_id(
        strategy_id="test-dup",
        decision_id=str(uuid.uuid4()),
        side="buy",
        qty=Decimal("1"),
        ticker="AAPL",
    )

    # First submit_order raises 422; lookup returns the cassette order.
    api_err = APIError("duplicate client_order_id", http_error=None)
    type(api_err).status_code = property(lambda self: 422)  # type: ignore[misc]
    alpaca_broker_cassette._tc_mock.return_value.submit_order.side_effect = api_err

    existing_order = _make_order_mock(cassette["limit_order_AAPL"], cid)
    alpaca_broker_cassette._tc_mock.return_value.get_order_by_client_id.return_value = (
        existing_order
    )

    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("180.45"),
        time_in_force=TimeInForce.DAY,
        client_order_id=cid,
    )
    result = await alpaca_broker_cassette.place_order(req)

    # Returned the existing order, not a re-submit
    assert result.broker_order_id == "broker-order-LIMIT-CASSETTE"
    assert result.client_order_id == cid
    # submit_order called exactly once — NEVER retried.
    assert alpaca_broker_cassette._tc_mock.return_value.submit_order.call_count == 1


# ---------------------------------------------------------------------------
# BROK-A-06: TradingStream fill event
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alpaca_paper_trading_stream_receives_fill_event_cassette(
    mocker: Any, cassette: dict[str, Any]
) -> None:
    """Feed the recorded fill payload directly through the stream's callback.

    The cassette replay does NOT open a real websocket. The TradingStream
    is mocked; we register the on_fill callback by constructing the
    AlpacaFillStream, then invoke ``_handle_trade_update`` directly with
    the recorded fill payload.
    """
    from gekko.brokers.stream import AlpacaFillStream

    mocker.patch("gekko.brokers.stream.TradingStream")

    captured: list[dict[str, Any]] = []

    async def on_fill(payload: dict[str, Any]) -> None:
        captured.append(payload)

    stream = AlpacaFillStream(
        api_key="cassette-key",
        secret_key="cassette-secret",
        user_id="test-user",
        on_fill=on_fill,
    )

    # Build the fill TradeUpdate shape from the cassette and route it.
    fill_payload = cassette["fill_event"]
    cid = compute_test_cid()
    order_mock = _make_order_mock(fill_payload["order"], cid)
    data = MagicMock()
    data.event = fill_payload["event"]
    data.order = order_mock

    await stream._handle_trade_update(data)

    assert len(captured) == 1
    fp = captured[0]
    assert fp["event"] == "fill"
    assert fp["client_order_id"] == cid
    assert fp["ticker"] == "AAPL"
    assert fp["user_id"] == "test-user"
    assert fp["filled_qty"] == "1"
    assert fp["filled_avg_price"] == "200.50"


def compute_test_cid() -> str:
    """Helper to produce a stable test client_order_id."""
    from gekko.core.ids import compute_client_order_id

    return compute_client_order_id(
        strategy_id="test-stream",
        decision_id="d-stream-fixed",
        side="buy",
        qty=Decimal("1"),
        ticker="AAPL",
    )


# ---------------------------------------------------------------------------
# Live mode — guarded by GEKKO_TEST_LIVE_ALPACA=1 + market hours
# ---------------------------------------------------------------------------


def _market_is_open() -> bool:
    """Inline NYSE market-hours check (avoids circular import on Plan 01-08)."""
    try:
        import datetime as dt

        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        now = dt.datetime.now(dt.UTC)
        schedule = nyse.schedule(start_date=now.date(), end_date=now.date())
        if schedule.empty:
            return False
        open_ts = schedule.iloc[0]["market_open"].to_pydatetime()
        close_ts = schedule.iloc[0]["market_close"].to_pydatetime()
    except Exception:  # noqa: BLE001 - skip live test if check fails
        return False
    else:
        return open_ts <= now <= close_ts


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_MODE, reason="GEKKO_TEST_LIVE_ALPACA not set")
async def test_alpaca_paper_place_limit_order_and_cancel_live(alpaca_broker_live: Any) -> None:
    """Live paper round trip: place limit (off-market), retrieve, cancel."""
    from gekko.brokers.base import OrderRequest
    from gekko.core.ids import compute_client_order_id
    from gekko.core.types import OrderSide, OrderType, TimeInForce

    # Get the current ask so we can place a no-fill limit 10% below.
    quote = await alpaca_broker_live.get_quote("AAPL")
    ask_decimal = Decimal(str(quote.get("ask_price", "200")))
    limit_price = (ask_decimal * Decimal("0.9")).quantize(Decimal("0.01"))

    cid = compute_client_order_id(
        strategy_id="live-test",
        decision_id=str(uuid.uuid4()),
        side="buy",
        qty=Decimal("1"),
        ticker="AAPL",
    )
    req = OrderRequest(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        time_in_force=TimeInForce.DAY,
        client_order_id=cid,
    )
    result = await alpaca_broker_live.place_order(req)
    try:
        # Retrieve by client_order_id
        fetched = await alpaca_broker_live.get_order_by_client_order_id(cid)
        assert fetched is not None
        assert fetched.client_order_id == cid
    finally:
        await alpaca_broker_live.cancel_order(result.broker_order_id)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not LIVE_MODE or not _market_is_open(),
    reason="Live fill-stream test requires GEKKO_TEST_LIVE_ALPACA=1 AND market open",
)
async def test_alpaca_paper_trading_stream_receives_fill_event_live(
    alpaca_broker_live: Any,
) -> None:
    """Live websocket fill — places a MARKET order during market hours and
    waits for the fill event to arrive on the stream.
    """
    import asyncio

    from gekko.brokers.base import OrderRequest
    from gekko.brokers.stream import AlpacaFillStream
    from gekko.core.ids import compute_client_order_id
    from gekko.core.types import OrderSide, OrderType

    captured: list[dict[str, Any]] = []
    event = asyncio.Event()

    async def on_fill(payload: dict[str, Any]) -> None:
        captured.append(payload)
        event.set()

    stream = AlpacaFillStream(
        api_key=os.environ["ALPACA_PAPER_API_KEY"],
        secret_key=os.environ["ALPACA_PAPER_SECRET_KEY"],
        user_id="live-user",
        on_fill=on_fill,
    )
    stream.start()

    try:
        cid = compute_client_order_id(
            strategy_id="live-fill",
            decision_id=str(uuid.uuid4()),
            side="buy",
            qty=Decimal("1"),
            ticker="AAPL",
        )
        req = OrderRequest(
            symbol="AAPL",
            side=OrderSide.BUY,
            qty=Decimal("1"),
            order_type=OrderType.MARKET,
            client_order_id=cid,
        )
        await alpaca_broker_live.place_order(req)
        await asyncio.wait_for(event.wait(), timeout=30.0)
        assert captured
        assert captured[0]["client_order_id"] == cid
    finally:
        await stream.stop()
