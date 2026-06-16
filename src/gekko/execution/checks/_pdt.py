"""Pattern Day Trader BLOCK check — Plan 02-03 Task 2 (D-29 / EXEC-11).

FINRA's Pattern Day Trader rule applies to margin accounts with <$25K equity
that execute 4+ day-trades within 5 business days. A round-trip = same-day
BUY+SELL of the same ticker. Triggering it leaves the account day-trade-
restricted for 90 days unless the user wires more cash.

Two-source detection per RESEARCH §4 (defense in depth):

  1. **Broker source (primary):** Alpaca's ``TradeAccount.pattern_day_trader``
     bool + ``daytrade_count`` running count. When the broker has already
     flagged the account AND ``daytrade_count >= 3`` (the 4th day-trade is
     what locks in PDT status) AND ``equity < $25K``, BLOCK with
     ``reject_code='pdt_rule'``.

  2. **Local source (defense in depth):** Walk the per-user ``events``
     table for ``fill`` events over a rolling 5-business-day window,
     count days where BOTH a BUY-side and SELL-side fill exist for the
     same ticker (a round-trip). When local count >= 3 AND equity <
     $25K AND this order would complete a 4th round-trip, BLOCK with
     ``reject_code='pdt_rule_local'``. Survives broker-side stale-cache
     AND extends to future brokers (P8 IBKR/Schwab) where the broker-
     side ``daytrade_count`` may not be exposed.

The local count uses ``pandas_market_calendars`` for business-day
arithmetic (Phase-1 already depends on it for ``is_market_open``) — a
7-calendar-day-old round-trip on a holiday-adjacent date IS within the
5-business-day window.

References:
  * .planning/phases/02-orderguard.../02-RESEARCH.md  §4 (PDT detection)
  * .planning/phases/02-orderguard.../02-PATTERNS.md  §1a row 8
  * https://www.finra.org/investors/learn-to-invest/advanced-investing/day-trading
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas_market_calendars as mcal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.brokers.base import OrderRequest
from gekko.config import get_settings
from gekko.core.errors import OrderGuardRejected
from gekko.db.engine import get_async_engine
from gekko.db.models import Event
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: Equity threshold under which PDT restrictions apply (per FINRA).
_PDT_EQUITY_THRESHOLD = Decimal("25000")

#: Number of round-trips in the 5-business-day window that locks PDT.
#: 3 prior + the current 4th day-trade = PDT-restricted account.
_PDT_ROUND_TRIP_THRESHOLD = 3


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Per-user session factory + engine (PATTERNS §3c test seam).

    Mirrors :func:`gekko.execution.checks._hard_caps._get_session_factory`
    so tests have a per-module monkeypatch seam.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


def _business_day_window_start(lookback_days: int) -> datetime:
    """Return the UTC datetime ``lookback_days`` NYSE business days ago.

    Uses ``pandas_market_calendars`` so holidays + half-days are honored.
    A holiday-adjacent 7-calendar-day-old fill IS still within the
    5-business-day window. Returns a tz-aware UTC datetime.
    """
    now_utc = datetime.now(UTC)
    cal = mcal.get_calendar("NYSE")
    # Look back enough calendar days that we cover the worst-case holiday
    # cluster. 5 business days could span up to 9 calendar days (Thanksgiving
    # weekend); 14 is comfortably safe.
    start_calendar = (now_utc - timedelta(days=14)).date()
    end_calendar = now_utc.date()
    sched = cal.schedule(start_date=start_calendar, end_date=end_calendar)
    if sched.empty or len(sched) <= lookback_days:
        # Edge case: not enough sessions in the lookback window. Fall back
        # to the calendar-days lower bound.
        return now_utc - timedelta(days=lookback_days)
    # ``schedule`` rows are sorted ascending; the last ``lookback_days``
    # rows are the most recent business days. The window starts at the
    # ``market_open`` of the lookback_days-th-most-recent session.
    target_row = sched.iloc[-lookback_days]
    start_ts = target_row["market_open"].to_pydatetime()
    if start_ts.tzinfo is None:
        start_ts = start_ts.replace(tzinfo=UTC)
    return start_ts


async def _walk_fills_in_window(
    user_id: str, window_start: datetime
) -> list[dict[str, Any]]:
    """Return parsed fill-event payloads for ``user_id`` since ``window_start``.

    Walks the ``events`` table for ``event_type='fill'`` rows, parses the
    ``payload_json`` (canonical-subset shape from Plan 01-04), and returns
    a list of inner ``payload`` dicts with the ``ts`` field merged in.
    """
    window_start_iso = window_start.isoformat()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event).where(
                        Event.user_id == user_id,
                        Event.event_type == "fill",
                        Event.ts >= window_start_iso,
                    )
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    fills: list[dict[str, Any]] = []
    for row in rows:
        try:
            outer = json.loads(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        # canonical-subset shape: {event_type, payload, ts, user_id}
        payload = outer.get("payload", outer)
        if not isinstance(payload, dict):
            continue
        # Surface row-level ts on the inner payload for date-bucketing.
        fills.append({**payload, "_event_ts": row.ts})
    return fills


def _count_round_trips(fills: list[dict[str, Any]]) -> int:
    """Count round-trips (same-day BUY+SELL of same ticker) in ``fills``.

    A round-trip on day D for ticker T = at least one fill with side=buy
    AND at least one fill with side=sell, both with ticker=T and same
    NYSE-local calendar date.
    """
    # Bucket: {(date_str, ticker): {"buy": bool, "sell": bool}}
    buckets: dict[tuple[str, str], dict[str, bool]] = {}
    for fill in fills:
        ticker_raw = fill.get("ticker") or fill.get("symbol")
        side_raw = fill.get("side")
        ts_raw = fill.get("_event_ts") or fill.get("ts")
        if not ticker_raw or not side_raw or not ts_raw:
            continue
        ticker = str(ticker_raw).upper()
        side = str(side_raw).lower()
        if side not in ("buy", "sell"):
            continue
        # Use the date portion (YYYY-MM-DD) — UTC ISO timestamps sort
        # correctly; round-trip detection requires same-day match.
        date_str = str(ts_raw)[:10]
        key = (date_str, ticker)
        bucket = buckets.setdefault(key, {"buy": False, "sell": False})
        bucket[side] = True

    return sum(
        1 for bucket in buckets.values() if bucket["buy"] and bucket["sell"]
    )


def _would_be_round_trip(req: OrderRequest, fills: list[dict[str, Any]]) -> bool:
    """Return True if ``req`` would complete a same-day round-trip today.

    Checks today's fills for an OPPOSITE-side fill on the same ticker.
    BUY req + prior SELL today (same ticker) -> round-trip. Same for the
    inverse.
    """
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    req_ticker = req.symbol.upper()
    req_side = req.side.value.lower()  # "buy" / "sell"
    opposite = "sell" if req_side == "buy" else "buy"
    for fill in fills:
        ticker_raw = fill.get("ticker") or fill.get("symbol")
        side_raw = fill.get("side")
        ts_raw = fill.get("_event_ts") or fill.get("ts")
        if not ticker_raw or not side_raw or not ts_raw:
            continue
        ticker = str(ticker_raw).upper()
        side = str(side_raw).lower()
        date_str = str(ts_raw)[:10]
        if ticker == req_ticker and side == opposite and date_str == today_str:
            return True
    return False


async def check_pdt(
    *,
    req: OrderRequest,
    account: dict[str, Any],
    user_id: str,
) -> None:
    """Block when this order would trigger PDT restrictions.

    Two-source defense (RESEARCH §4):

    * **Broker primary:** ``pattern_day_trader=True`` AND
      ``daytrade_count >= 3`` AND ``equity < $25K``.
    * **Local defense:** local 5-business-day round-trip count >= 3 AND
      ``equity < $25K`` AND this order would complete a 4th round-trip.

    :param req: The :class:`OrderRequest` about to be sent.
    :param account: Output of ``broker.get_account()`` — the broker's
        view of the trading account.
    :param user_id: Per-user SQLCipher DB scope.
    :raises OrderGuardRejected:
        * ``reject_code='pdt_rule'`` — broker source fired.
        * ``reject_code='pdt_rule_local'`` — local source fired.
    """
    pdt_flag = account.get("pattern_day_trader") is True
    try:
        daytrade_count = int(account.get("daytrade_count") or 0)
    except (TypeError, ValueError):
        daytrade_count = 0
    equity = Decimal(str(account.get("equity") or "0"))

    # ---- Source 1: broker-side primary check -----------------------------
    if (
        pdt_flag
        and equity < _PDT_EQUITY_THRESHOLD
        and daytrade_count >= _PDT_ROUND_TRIP_THRESHOLD
    ):
        raise OrderGuardRejected(
            "pdt_rule",
            (
                f"Pattern Day Trader rule would block this order: "
                f"pattern_day_trader={pdt_flag}, "
                f"daytrade_count={daytrade_count}, "
                f"equity={equity} < ${_PDT_EQUITY_THRESHOLD} minimum"
            ),
            extra={
                "ticker": req.symbol,
                "pattern_day_trader": pdt_flag,
                "daytrade_count": daytrade_count,
                "equity": str(equity),
            },
        )

    # ---- Source 2: local audit-log defense in depth ----------------------
    # Only walk the events table when equity is below the threshold AND the
    # broker hasn't already flagged the account (which would have been
    # caught above). Above $25K equity, PDT doesn't apply.
    if equity >= _PDT_EQUITY_THRESHOLD:
        return

    window_start = _business_day_window_start(lookback_days=5)
    fills = await _walk_fills_in_window(user_id, window_start)
    local_count = _count_round_trips(fills)

    if (
        local_count >= _PDT_ROUND_TRIP_THRESHOLD
        and _would_be_round_trip(req, fills)
    ):
        raise OrderGuardRejected(
            "pdt_rule_local",
            (
                f"Local 5-business-day round-trip count is {local_count} "
                f"and this order would complete a 4th round-trip in "
                f"{req.symbol}; account equity {equity} < "
                f"${_PDT_EQUITY_THRESHOLD} minimum"
            ),
            extra={
                "ticker": req.symbol,
                "local_round_trip_count": local_count,
                "equity": str(equity),
            },
        )


__all__: tuple[str, ...] = ("check_pdt",)
