"""AlpacaBroker constructor paper-only guard — Plan 01-05 Task 2.

Pitfall 7 from PITFALLS.md / D-24 from CONTEXT.md: Phase 1 is paper-only.
Live trading is physically rejected at construction time, BEFORE any
TradingClient is instantiated. Two layers of defense:

1. **Argument check.** ``AlpacaBroker(paper=False)`` raises
   ``BrokerConfigError`` immediately. The TradingClient is NEVER
   constructed in this branch.

2. **Post-construct probe.** After building the TradingClient, the
   constructor reads ``client._base_url`` (per alpaca-py 0.43, this is a
   ``BaseURL`` enum whose ``.value`` contains "paper" for the paper
   endpoint). If the URL does not look paper-y, raise
   ``BrokerConfigError`` even though the caller passed ``paper=True`` —
   defense against a future alpaca-py change that flips the meaning of
   that argument.

The unit tests mock ``alpaca.trading.client.TradingClient`` so they run
offline. The live two-layer assertion is exercised end-to-end in Task 4's
integration test.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Argument-check layer (the load-bearing P1 invariant)
# ---------------------------------------------------------------------------


def test_paper_false_rejected_before_trading_client_constructed(mocker: Any) -> None:
    """``AlpacaBroker(paper=False)`` raises BEFORE building the TradingClient.

    The mock confirms ``TradingClient.__init__`` was never called.

    Plan 02-06 Task 1 (BLOCKER #4): the error message changed because
    Phase 2 permits live mode via the internal ``_allow_live=True`` opt-in.
    Naive ``AlpacaBroker(paper=False)`` from user code STILL raises;
    the message now points at ``_build_broker`` as the only vetted site.
    """
    from gekko.brokers.alpaca import AlpacaBroker
    from gekko.core.errors import BrokerConfigError

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")

    with pytest.raises(BrokerConfigError) as excinfo:
        AlpacaBroker(api_key="x", secret_key="y", paper=False)

    # Message points at the vetted live path.
    msg = str(excinfo.value)
    assert "live" in msg.lower()
    assert "_build_broker" in msg or "_allow_live" in msg

    # TradingClient was NEVER constructed (Phase-1 invariant preserved
    # — even with the Phase-2 live opt-in available, the guard still
    # short-circuits naive callers).
    tc_mock.assert_not_called()


def test_paper_defaults_to_true(mocker: Any) -> None:
    """Omitting ``paper`` keyword defaults to ``True`` — defensive default."""
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    # mock the get_account call so the post-construct probe doesn't fail
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="paper-acct-abc")
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://paper-api.alpaca.markets/v2"

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    broker = AlpacaBroker(api_key="x", secret_key="y")
    assert broker.is_paper is True


def test_paper_true_succeeds(mocker: Any) -> None:
    """``AlpacaBroker(paper=True)`` constructs and exposes the expected attrs."""
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="paper-acct-abc")
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://paper-api.alpaca.markets/v2"

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    broker = AlpacaBroker(api_key="x", secret_key="y", paper=True)
    assert broker.is_paper is True
    assert broker.name == "alpaca"
    assert broker.supports_fractional is True


# ---------------------------------------------------------------------------
# Post-construct probe layer (defense-in-depth)
# ---------------------------------------------------------------------------


def test_post_construct_probe_rejects_non_paper_base_url(mocker: Any) -> None:
    """If the constructed client has a non-paper base URL, raise.

    Even with ``paper=True`` from the caller, if the TradingClient's
    ``_base_url`` does not contain "paper" we refuse to proceed. Guards
    against a future alpaca-py version that flips the argument's meaning
    or a corrupted env that swaps paper for live keys silently.
    """
    from gekko.brokers.alpaca import AlpacaBroker
    from gekko.core.errors import BrokerConfigError

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    # Force the probe to see a live-looking URL.
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://api.alpaca.markets/v2"
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="live-acct-xyz")

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    with pytest.raises(BrokerConfigError) as excinfo:
        AlpacaBroker(api_key="x", secret_key="y", paper=True)

    assert "Paper-mode assertion" in str(excinfo.value)


def test_post_construct_probe_accepts_paper_base_url(mocker: Any) -> None:
    """A paper-shaped base URL passes the probe."""
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    tc_mock.return_value._base_url = mocker.Mock()
    tc_mock.return_value._base_url.value = "https://paper-api.alpaca.markets/v2"
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="paper-acct-abc")

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    broker = AlpacaBroker(api_key="x", secret_key="y", paper=True)
    assert broker.is_paper is True


# ---------------------------------------------------------------------------
# alpaca-py BaseURL enum — the probe handles both .value access and bare str
# ---------------------------------------------------------------------------


def test_probe_handles_baseurl_enum_repr(mocker: Any) -> None:
    """The probe compares against ``str(client._base_url)`` so the BaseURL enum's
    repr ('BaseURL.TRADING_PAPER') is also accepted.

    Belt-and-suspenders: in alpaca-py 0.43, ``client._base_url`` is the
    ``BaseURL`` enum instance, not a string. Its ``__str__`` is the
    member name (``BaseURL.TRADING_PAPER``), and its ``.value`` is the
    URL string (``https://paper-api.alpaca.markets/v2``). The probe
    should tolerate both forms.
    """
    from gekko.brokers.alpaca import AlpacaBroker

    tc_mock = mocker.patch("gekko.brokers.alpaca.TradingClient")
    # Simulate the BaseURL enum: str() returns "BaseURL.TRADING_PAPER",
    # .value returns the URL.

    class _FakeBaseURL:
        value = "https://paper-api.alpaca.markets/v2"

        def __str__(self) -> str:
            return "BaseURL.TRADING_PAPER"

    tc_mock.return_value._base_url = _FakeBaseURL()
    tc_mock.return_value.get_account.return_value = mocker.Mock(id="paper-acct-abc")

    mocker.patch("gekko.brokers.alpaca.StockHistoricalDataClient")

    broker = AlpacaBroker(api_key="x", secret_key="y", paper=True)
    assert broker.is_paper is True
