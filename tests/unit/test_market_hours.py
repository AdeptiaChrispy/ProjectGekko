"""Tests for ``gekko.execution.market_hours`` — Plan 01-08 Task 2.

Per the plan's <behavior> block, 9 behaviors covering:

1. Regular RTH (14:30 ET on a weekday).
2. Pre-open (08:00 ET).
3. After-close (17:00 ET).
4. Weekend (Saturday).
5. NYSE holiday (July 4 2026 — Independence Day, observed).
6. Half-day after early close (Black Friday 2026, if applicable).
7. next_market_open returns the next valid RTH open.
8. Naive datetime is interpreted as UTC.
9. tzdata is available — ZoneInfo("America/New_York") does not raise
   (Pitfall 5 Windows guard).

EXEC-10 is the only safety control besides the broker constructor guard
that lives in Phase 1 — these tests are the gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

from gekko.execution.market_hours import is_market_open, next_market_open

NYSE_TZ = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Behavior 9 — tzdata sanity
# ---------------------------------------------------------------------------


def test_tzdata_available_on_this_platform() -> None:
    """ZoneInfo('America/New_York') must work — Pitfall 5 Windows guard."""
    tz = ZoneInfo("America/New_York")
    # Smoke: turn it into a datetime and back
    sample = datetime(2026, 6, 8, 14, 30, tzinfo=tz)
    assert sample.tzinfo is tz


# ---------------------------------------------------------------------------
# Behaviors 1-3 — regular weekday window
# ---------------------------------------------------------------------------


def test_market_open_at_rth_1430_et_monday() -> None:
    """Mon 2026-06-08 14:30 ET — inside RTH 09:30-16:00."""
    # 2026-06-08 is a Monday; not a holiday.
    when = datetime(2026, 6, 8, 14, 30, tzinfo=NYSE_TZ)
    assert is_market_open(when) is True


def test_market_closed_at_0800_et_pre_open() -> None:
    """Mon 2026-06-08 08:00 ET — before 09:30 open."""
    when = datetime(2026, 6, 8, 8, 0, tzinfo=NYSE_TZ)
    assert is_market_open(when) is False


def test_market_closed_at_1700_et_after_close() -> None:
    """Mon 2026-06-08 17:00 ET — after 16:00 close."""
    when = datetime(2026, 6, 8, 17, 0, tzinfo=NYSE_TZ)
    assert is_market_open(when) is False


# ---------------------------------------------------------------------------
# Behavior 4 — weekend
# ---------------------------------------------------------------------------


def test_market_closed_on_saturday() -> None:
    """Sat 2026-06-06 14:30 ET — NYSE closed on weekends."""
    when = datetime(2026, 6, 6, 14, 30, tzinfo=NYSE_TZ)
    assert is_market_open(when) is False


# ---------------------------------------------------------------------------
# Behavior 5 — NYSE holiday
# ---------------------------------------------------------------------------


def test_market_closed_on_july_4_independence_day() -> None:
    """July 4 is the canonical NYSE holiday. 2026-07-04 is a Saturday so the
    observed holiday falls on Friday 2026-07-03."""
    cal = mcal.get_calendar("NYSE")
    # Sanity: confirm 2026-07-03 is in the holiday set (observed Independence Day)
    observed_holidays = cal.holidays().holidays
    # Convert numpy.datetime64 to date for comparison
    from datetime import date

    holiday_dates = {
        h.astype("M8[D]").astype(date) for h in observed_holidays
    }
    assert date(2026, 7, 3) in holiday_dates

    # 2026-07-03 14:30 ET — observed Independence Day; market closed.
    when = datetime(2026, 7, 3, 14, 30, tzinfo=NYSE_TZ)
    assert is_market_open(when) is False


# ---------------------------------------------------------------------------
# Behavior 6 — half-day early close
# ---------------------------------------------------------------------------


def test_market_closed_after_black_friday_early_close() -> None:
    """Black Friday 2026 (2026-11-27) is a NYSE half-day with 13:00 ET close.

    At 14:00 ET the market is closed even though the date itself is a trading
    day. Verified against pandas_market_calendars's NYSE special_closes data.
    """
    # 2026-11-27 14:00 ET — after 13:00 early close
    when = datetime(2026, 11, 27, 14, 0, tzinfo=NYSE_TZ)
    assert is_market_open(when) is False

    # And at 12:00 ET it should still be open (sanity that the half-day
    # is recognized at all, not the entire day being closed)
    when_open = datetime(2026, 11, 27, 12, 0, tzinfo=NYSE_TZ)
    assert is_market_open(when_open) is True


# ---------------------------------------------------------------------------
# Behavior 7 — next_market_open
# ---------------------------------------------------------------------------


def test_next_market_open_from_saturday_returns_monday_0930_et() -> None:
    """Saturday morning -> next session opens Monday 09:30 ET."""
    # Sat 2026-06-06 10:00 ET — next session is Mon 2026-06-08 09:30 ET
    when = datetime(2026, 6, 6, 10, 0, tzinfo=NYSE_TZ)
    nxt = next_market_open(when)
    # Compare in NYSE-local terms
    nxt_local = nxt.astimezone(NYSE_TZ)
    assert nxt_local.year == 2026
    assert nxt_local.month == 6
    assert nxt_local.day == 8
    assert nxt_local.hour == 9
    assert nxt_local.minute == 30


# ---------------------------------------------------------------------------
# Behavior 8 — naive datetime treated as UTC
# ---------------------------------------------------------------------------


def test_naive_datetime_treated_as_utc() -> None:
    """Naive datetime is treated as UTC and converted to NY local for comparison.

    2026-06-08 18:30 UTC == 14:30 EDT (NY observes EDT in June). Should be RTH.
    """
    when_naive = datetime(2026, 6, 8, 18, 30)  # 14:30 EDT
    assert is_market_open(when_naive) is True

    # And the same datetime, made explicit, should give the same result
    when_aware = datetime(2026, 6, 8, 18, 30, tzinfo=timezone.utc)
    assert is_market_open(when_aware) is True
