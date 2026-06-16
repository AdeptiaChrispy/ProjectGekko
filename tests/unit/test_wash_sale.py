"""Wash-sale FLAG path tests — Plan 02-03 Task 3 (EXEC-09 / IRC §1091).

Covers the FLAG-only contract:

* 30-day lookback over local ``events.fill`` rows.
* Same-ticker match within window -> returns flag dict.
* Outside window / different ticker -> returns None.
* NEVER raises (tripwire test confirms — PATTERNS §4 anti-pattern row 12).

The function returns ``dict | None``; OrderGuard does NOT call it
(wash-sale is surfaced at proposal-build time by ProposalWriter, NOT
re-checked at place_order time).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from gekko.audit.log import append_event
from gekko.brokers.base import OrderRequest
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.execution.checks._wash_sale import flag_wash_sale


@pytest_asyncio.fixture
async def seeded_engine(temp_sqlcipher_db: Any) -> AsyncIterator[Any]:
    """SQLCipher engine + seeded ``users`` row + patched session factory."""
    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    yield temp_sqlcipher_db


def _patch_session_factory(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """Patch the wash-sale module's ``_get_session_factory``."""
    from gekko.execution.checks import _wash_sale as ws_mod

    sf = make_session_factory(engine)
    monkeypatch.setattr(
        ws_mod,
        "_get_session_factory",
        lambda user_id: (sf, None),
        raising=True,
    )


def _buy_req(symbol: str = "AAPL") -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        stop_price=None,
        time_in_force=TimeInForce.DAY,
        client_order_id="0" * 32,
    )


async def _seed_fill(
    engine: Any,
    *,
    user_id: str,
    ticker: str,
    side: str,
    days_ago: int,
    qty: str = "10",
) -> int:
    """Seed a single ``fill`` event ``days_ago`` calendar days in the past.

    Returns the event row id for assertion correlation.
    """
    sf = make_session_factory(engine)
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    async with sf() as session, session.begin():
        row = await append_event(
            session,
            user_id=user_id,
            strategy_id=None,
            event_type="fill",
            payload={
                "ticker": ticker,
                "side": side,
                "filled_qty": qty,
            },
            ts=ts,
        )
        return row.id


# ---------------------------------------------------------------------------
# Same-ticker within 30-day window -> flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_flags_same_ticker_within_window(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SELL fill 15 days ago on AAPL + new BUY AAPL -> flag dict."""
    _patch_session_factory(monkeypatch, seeded_engine)
    event_id = await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="AAPL",
        side="sell",
        days_ago=15,
    )

    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is not None
    assert flag["would_be_wash_sale"] is True
    assert flag["lookback_event_id"] == event_id
    assert flag["ticker"] == "AAPL"
    assert flag["lookback_side"] == "sell"
    assert "wash sale" in flag["note"].lower()


@pytest.mark.asyncio
async def test_wash_sale_flags_when_seeded_today(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fill 0 days ago (today) — within window."""
    _patch_session_factory(monkeypatch, seeded_engine)
    await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="AAPL",
        side="buy",
        days_ago=0,
    )

    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is not None
    assert flag["ticker"] == "AAPL"
    assert flag["lookback_side"] == "buy"


# ---------------------------------------------------------------------------
# Outside 30-day window -> no flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_no_flag_outside_30_day_window(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fill 31 days ago — outside IRC §1091 window. Returns None."""
    _patch_session_factory(monkeypatch, seeded_engine)
    await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="AAPL",
        side="sell",
        days_ago=31,
    )

    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is None


# ---------------------------------------------------------------------------
# Different ticker -> no flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_no_flag_for_different_ticker(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fill on MSFT 15 days ago + new BUY AAPL -> different ticker. None."""
    _patch_session_factory(monkeypatch, seeded_engine)
    await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="MSFT",
        side="sell",
        days_ago=15,
    )

    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is None


# ---------------------------------------------------------------------------
# Many fills - bounded scan picks first same-ticker match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_bounded_scan_picks_first_match(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed 5 fills with only one matching ticker — bounded scan still
    returns the match."""
    _patch_session_factory(monkeypatch, seeded_engine)
    # 4 non-matching MSFT fills.
    for d in range(1, 5):
        await _seed_fill(
            seeded_engine,
            user_id="test-user",
            ticker="MSFT",
            side="buy",
            days_ago=d,
        )
    # 1 matching AAPL fill.
    aapl_id = await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="AAPL",
        side="sell",
        days_ago=20,
    )

    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is not None
    assert flag["lookback_event_id"] == aapl_id


# ---------------------------------------------------------------------------
# NEVER raises — tripwire (PATTERNS §4 anti-pattern row 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wash_sale_never_raises_on_db_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the DB walk fails (e.g., session factory broken), the function
    MUST NOT raise — wash-sale is FLAG-only. Returns None on any failure."""
    from gekko.execution.checks import _wash_sale as ws_mod

    def _broken_factory(_user_id: str) -> Any:
        raise RuntimeError("synthetic DB failure")

    monkeypatch.setattr(
        ws_mod, "_get_session_factory", _broken_factory, raising=True
    )

    # Must not raise.
    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    assert flag is None


@pytest.mark.asyncio
async def test_wash_sale_never_raises_on_malformed_payload(
    seeded_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with a malformed payload_json the function does not raise."""
    from sqlalchemy import update

    from gekko.db.models import Event

    _patch_session_factory(monkeypatch, seeded_engine)
    event_id = await _seed_fill(
        seeded_engine,
        user_id="test-user",
        ticker="AAPL",
        side="sell",
        days_ago=10,
    )

    # Corrupt the payload_json directly.
    sf = make_session_factory(seeded_engine)
    async with sf() as session, session.begin():
        await session.execute(
            update(Event)
            .where(Event.id == event_id)
            .values(payload_json="<<<malformed json>>>")
        )

    # Must not raise — corrupted rows are silently skipped.
    flag = await flag_wash_sale(req=_buy_req("AAPL"), user_id="test-user")
    # No match found because the only seeded row is malformed.
    assert flag is None


# ---------------------------------------------------------------------------
# Signature contract
# ---------------------------------------------------------------------------


def test_wash_sale_signature_is_dict_or_none() -> None:
    """The return annotation MUST be ``dict | None`` — the FLAG-only contract.

    Locked per RESEARCH §5 and PATTERNS §4 anti-pattern row 12.
    """
    sig = inspect.signature(flag_wash_sale)
    ret = str(sig.return_annotation)
    # Accept "dict[str, Any] | None" or "Optional[dict[str, Any]]" — any
    # union shape that includes both dict and None.
    assert "dict" in ret and "None" in ret, (
        f"flag_wash_sale return annotation must be dict | None; got {ret!r}"
    )
