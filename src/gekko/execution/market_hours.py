"""NYSE market-hours guard (EXEC-10) — Plan 01-08 Task 2.

Wraps ``pandas_market_calendars`` so the Executor can ask a single
question: "is the market open *right now*?" before submitting an order.
Phase 1's only safety control besides the broker constructor guard
(:class:`gekko.brokers.alpaca.AlpacaBroker`).

Key behaviors:

* Regular trading hours (09:30-16:00 ET on weekdays) -> True.
* Pre-open / after-close / weekends / NYSE holidays -> False.
* Half-day early closes (e.g., 2026-11-27 Black Friday closes at 13:00 ET)
  are honored — ``is_market_open`` returns False after the early close
  even though the date itself is a trading day.
* Naive datetimes are interpreted as UTC and converted to NYSE-local for
  the schedule lookup. The docstring documents this contract.

``pandas_market_calendars`` handles the data — we never hand-roll
holiday lists. Per RESEARCH §"Don't Hand-Roll": "Holidays, half-days,
early closes are non-obvious; package ships them".

Cross-platform note (Pitfall 5): ``zoneinfo.ZoneInfo("America/New_York")``
requires ``tzdata`` on Windows. The ``tzdata`` package is an explicit
dependency in pyproject.toml so this import never fails at runtime.

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  EXEC-10 (market-hours
    guard); D-08 (daily schedule per strategy)
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Don't Hand-Roll" —
    pandas_market_calendars
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

#: The NYSE trading timezone. Used both for schedule lookups and for
#: converting naive datetimes into local time for the calendar query.
NYSE_TZ = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _nyse_calendar() -> Any:
    """Return the cached NYSE :class:`MarketCalendar` instance.

    Cached at module level because constructing the calendar parses every
    NYSE holiday + special-close rule and we hit ``is_market_open`` on
    every Executor invocation. The cache is a single entry — there's only
    one NYSE.
    """
    return mcal.get_calendar("NYSE")


def _to_aware_utc(now: datetime | None) -> datetime:
    """Coerce ``now`` to a timezone-aware UTC datetime.

    Defaults to ``datetime.now(UTC)`` when ``now`` is None. Naive inputs
    are treated as UTC (the docstring on each public function carries the
    same caveat).
    """
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def is_market_open(now: datetime | None = None) -> bool:
    """Return True iff the NYSE is currently in regular trading hours.

    Regular trading hours = 09:30-16:00 ET on a trading day, accounting
    for NYSE holidays and half-day early closes.

    :param now: Timestamp to evaluate. ``None`` defaults to
        ``datetime.now(UTC)``. Naive datetimes are interpreted as UTC.
    :returns: True when the NYSE is open at ``now``; False otherwise
        (pre-open, after-close, weekend, holiday, or after a half-day
        early close).
    """
    now_utc = _to_aware_utc(now)
    cal = _nyse_calendar()

    # Look up the schedule using the NYSE-local date — the calendar's
    # session boundaries are returned as UTC pandas Timestamps.
    local_date = now_utc.astimezone(NYSE_TZ).date()
    sched = cal.schedule(start_date=local_date, end_date=local_date)
    if sched.empty:
        # No session today — weekend or holiday.
        return False

    # ``market_open`` and ``market_close`` are pandas Timestamps in UTC.
    open_ts = sched.iloc[0]["market_open"].to_pydatetime()
    close_ts = sched.iloc[0]["market_close"].to_pydatetime()

    # Ensure the timestamps are tz-aware UTC for the comparison
    if open_ts.tzinfo is None:
        open_ts = open_ts.replace(tzinfo=timezone.utc)
    if close_ts.tzinfo is None:
        close_ts = close_ts.replace(tzinfo=timezone.utc)

    return open_ts <= now_utc < close_ts


def next_market_open(now: datetime | None = None) -> datetime:
    """Return the next NYSE session open after ``now`` (UTC).

    Looks up to 14 days ahead, which covers long weekends and the worst-
    case holiday cluster (Thanksgiving + Black Friday + extended weekend).

    :param now: Timestamp to look forward from. ``None`` defaults to
        ``datetime.now(UTC)``. Naive datetimes are interpreted as UTC.
    :returns: A timezone-aware UTC datetime representing the next session
        open. Caller can ``.astimezone(NYSE_TZ)`` to render it locally.
    :raises RuntimeError: If no NYSE session is found within 14 days
        (indicates a calendar-data issue and is unrecoverable here).
    """
    now_utc = _to_aware_utc(now)
    cal = _nyse_calendar()

    local_date = now_utc.astimezone(NYSE_TZ).date()
    end_date = local_date + timedelta(days=14)
    sched = cal.schedule(start_date=local_date, end_date=end_date)
    for _, row in sched.iterrows():
        open_ts = row["market_open"].to_pydatetime()
        if open_ts.tzinfo is None:
            open_ts = open_ts.replace(tzinfo=timezone.utc)
        if open_ts > now_utc:
            return open_ts
    msg = (
        "No upcoming NYSE session found within 14 days — calendar data may "
        "be missing or pandas_market_calendars is misconfigured."
    )
    raise RuntimeError(msg)


__all__: tuple[str, ...] = (
    "NYSE_TZ",
    "is_market_open",
    "next_market_open",
)
