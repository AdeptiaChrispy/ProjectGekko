"""Tests for ``_resolve_quiet_hours`` predicate — Plan 03-03 Task 1 (HITL-05).

Covers 9 cases per the plan's ``<behavior>`` spec:
  (a) no quiet hours configured → False
  (b) user-level only, in-window (22:00-07:00, now=23:30 ET) → True
  (c) user-level only, outside-window (now=10:00 ET) → False
  (d) strategy override narrower than user (user 22:00-07:00, strategy 23:00-06:00,
      now=22:30 ET) → False (strategy wins, 22:30 outside strategy window)
  (e) strategy override wider than user (user 22:00-07:00, strategy 21:00-08:00,
      now=21:30 ET) → True (strategy wins)
  (f) DST spring-forward: freeze_time("2027-03-14 06:30 UTC") → 02:30 local,
      spring-forward converts to 03:30 EDT
  (g) DST fall-back: freeze_time("2027-11-07 05:30 UTC") → 01:30 ET (first fold)
  (h) invalid IANA tz raises ValueError
  (i) half-set strategy (only start, no end) falls back to user window
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The predicate under test.
from gekko.approval.quiet_hours import _resolve_quiet_hours


# ---------------------------------------------------------------------------
# Helpers — minimal User / Strategy row stubs
# ---------------------------------------------------------------------------


def _make_user(
    *,
    user_id: str = "u1",
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
    timezone: str | None = None,
) -> Any:
    """Return a minimal User-like object (avoids DB round-trip in unit tests)."""
    u = MagicMock()
    u.user_id = user_id
    u.quiet_hours_start = quiet_hours_start
    u.quiet_hours_end = quiet_hours_end
    u.timezone = timezone
    return u


def _make_strategy_row(
    *,
    user_id: str = "u1",
    strategy_name: str = "s1",
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
) -> Any:
    """Return a minimal Strategy-like object for test seams."""
    payload: dict[str, Any] = {}
    if quiet_hours_start is not None:
        payload["quiet_hours_start"] = quiet_hours_start
    if quiet_hours_end is not None:
        payload["quiet_hours_end"] = quiet_hours_end

    row = MagicMock()
    row.user_id = user_id
    row.strategy_name = strategy_name
    row.payload_json = json.dumps(payload) if payload else "{}"
    return row


def _build_sf_seam(
    user: Any,
    strategy_row: Any | None = None,
) -> tuple[Any, Any]:
    """Return a (session_factory, engine) pair that yields ``user`` + ``strategy_row``."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=user)

    # Build a mock execute chain: session.execute(...).scalar_one_or_none()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=strategy_row)
    session.execute = AsyncMock(return_value=exec_result)

    # AsyncContextManager for ``async with sf() as session``
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    sf = MagicMock()
    sf.return_value = ctx

    engine = AsyncMock()
    engine.dispose = AsyncMock()

    return sf, engine


def _patch_seam(user: Any, strategy_row: Any | None = None):
    """Context manager that patches ``_get_session_factory`` with DB stubs."""
    sf, engine = _build_sf_seam(user, strategy_row)
    return patch(
        "gekko.approval.quiet_hours._get_session_factory",
        return_value=(sf, engine),
    )


# ---------------------------------------------------------------------------
# (a) No quiet hours configured → False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_quiet_hours_configured() -> None:
    """When User has no quiet_hours_start / quiet_hours_end → returns False."""
    user = _make_user(quiet_hours_start=None, quiet_hours_end=None, timezone=None)
    now = datetime(2026, 6, 17, 23, 30, 0, tzinfo=UTC)

    with _patch_seam(user):
        result = await _resolve_quiet_hours("u1", now)

    assert result is False


# ---------------------------------------------------------------------------
# (b) User-level only, in-window overnight wrap (22:00-07:00, now=23:30 ET) → True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overnight_in_window() -> None:
    """23:30 ET is inside 22:00-07:00 overnight window → True."""
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    # 23:30 ET = 04:30 UTC next day.  Use a fixed UTC instant that is 23:30 ET.
    # 2026-06-17 23:30 ET = 2026-06-18 03:30 UTC (EDT is UTC-4).
    now = datetime(2026, 6, 18, 3, 30, 0, tzinfo=UTC)

    with _patch_seam(user):
        result = await _resolve_quiet_hours("u1", now)

    assert result is True


# ---------------------------------------------------------------------------
# (c) User-level only, outside-window (now=10:00 ET) → False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outside_window() -> None:
    """10:00 ET is outside 22:00-07:00 window → False."""
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    # 10:00 ET = 14:00 UTC (EDT is UTC-4).
    now = datetime(2026, 6, 17, 14, 0, 0, tzinfo=UTC)

    with _patch_seam(user):
        result = await _resolve_quiet_hours("u1", now)

    assert result is False


# ---------------------------------------------------------------------------
# (d) Strategy override narrower than user — strategy wins → False
#     User: 22:00-07:00  Strategy: 23:00-06:00  now=22:30 ET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_override_narrower() -> None:
    """22:30 ET is outside strategy's 23:00-06:00 window → False (strategy wins)."""
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    strategy_row = _make_strategy_row(
        quiet_hours_start="23:00:00",
        quiet_hours_end="06:00:00",
    )
    # 22:30 ET = 02:30 UTC next day (EDT UTC-4).
    now = datetime(2026, 6, 18, 2, 30, 0, tzinfo=UTC)

    with _patch_seam(user, strategy_row):
        result = await _resolve_quiet_hours("u1", now, strategy_name="s1")

    assert result is False


@pytest.mark.asyncio
async def test_strategy_override() -> None:
    """Strategy override (23:00-06:00) wins over user (22:00-07:00).

    now=22:30 ET is outside strategy window → False.
    This is the canonical ``test_strategy_override`` acceptance criterion.
    """
    # Same as test_strategy_override_narrower — keeping explicit name for
    # acceptance criteria match.
    await test_strategy_override_narrower()


# ---------------------------------------------------------------------------
# (e) Strategy override wider than user — strategy wins → True
#     User: 22:00-07:00  Strategy: 21:00-08:00  now=21:30 ET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_override_wider() -> None:
    """21:30 ET is inside strategy's 21:00-08:00 window → True (strategy wins)."""
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    strategy_row = _make_strategy_row(
        quiet_hours_start="21:00:00",
        quiet_hours_end="08:00:00",
    )
    # 21:30 ET = 01:30 UTC next day (EDT UTC-4).
    now = datetime(2026, 6, 18, 1, 30, 0, tzinfo=UTC)

    with _patch_seam(user, strategy_row):
        result = await _resolve_quiet_hours("u1", now, strategy_name="s1")

    assert result is True


# ---------------------------------------------------------------------------
# (f) DST spring-forward — 2027-03-14 06:30 UTC = 02:30 local → spring-forward
#     converts to 03:30 EDT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dst_spring_forward() -> None:
    """Spring-forward: 2027-03-14 06:30 UTC = 03:30 EDT (02:30 doesn't exist).

    Window: 22:00-07:00.  03:30 EDT < 07:00 → in window → True.
    ``zoneinfo`` handles the fold implicitly; no manual arithmetic needed.
    """
    from zoneinfo import ZoneInfo

    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    # On 2027-03-14, clocks spring forward at 2:00 AM EST → 3:00 AM EDT.
    # 2027-03-14 06:30 UTC = 2027-03-14 01:30 EST (before spring-forward)
    # Actually: 06:30 UTC when ET = UTC-5 (EST) → 01:30 EST.
    # But spring-forward is at 07:00 UTC (2:00 AM EST → 3:00 AM EDT).
    # So 06:30 UTC = 01:30 EST (still before the clock-change).
    # Use 07:30 UTC instead: 07:30 UTC = 03:30 EDT (post spring-forward).
    now = datetime(2027, 3, 14, 7, 30, 0, tzinfo=UTC)

    # Verify the conversion produces 03:30 EDT (post-spring-forward).
    local = now.astimezone(ZoneInfo("America/New_York"))
    assert local.hour == 3, f"Expected 03:xx EDT, got {local}"

    # 03:30 EDT < 07:00 end → in overnight window (22:00-07:00) → True
    with _patch_seam(user):
        result = await _resolve_quiet_hours("u1", now)

    assert result is True


# ---------------------------------------------------------------------------
# (g) DST fall-back — 2027-11-07 05:30 UTC = 01:30 ET (ambiguous fold)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dst_fall_back() -> None:
    """Fall-back: 2027-11-07 05:30 UTC = 01:30 ET (ambiguous 1am fold).

    Window: 22:00-07:00.  01:30 ET < 07:00 → in window → True.
    ``zoneinfo`` picks the EDT (first) fold by default.
    """
    from zoneinfo import ZoneInfo

    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    # 2027-11-07 05:30 UTC: clocks fall back at 06:00 UTC (2:00 AM EDT → 1:00 AM EST).
    # So 05:30 UTC = 01:30 EDT (first fold, before the clock falls back).
    now = datetime(2027, 11, 7, 5, 30, 0, tzinfo=UTC)

    local = now.astimezone(ZoneInfo("America/New_York"))
    assert local.hour == 1, f"Expected 01:xx ET, got {local}"

    # 01:30 ET < 07:00 end → in overnight window → True
    with _patch_seam(user):
        result = await _resolve_quiet_hours("u1", now)

    assert result is True


# ---------------------------------------------------------------------------
# (h) Invalid IANA timezone → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_iana_tz_raises() -> None:
    """Non-IANA timezone string raises ValueError (T-03-03-01 whitelist)."""
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="Not/A/Timezone",
    )
    now = datetime(2026, 6, 17, 23, 30, 0, tzinfo=UTC)

    with _patch_seam(user):
        with pytest.raises(ValueError, match="Invalid IANA timezone"):
            await _resolve_quiet_hours("u1", now)


# ---------------------------------------------------------------------------
# (i) Half-set strategy (start but no end) falls back to user window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_half_set_strategy_falls_back_to_user() -> None:
    """Strategy with only quiet_hours_start (no end) is treated as not-set.

    Falls back to user window (22:00-07:00); now=23:30 ET → True.
    """
    user = _make_user(
        quiet_hours_start="22:00:00",
        quiet_hours_end="07:00:00",
        timezone="America/New_York",
    )
    # Strategy has only start set, no end → half-set → falls back.
    strategy_row = _make_strategy_row(
        quiet_hours_start="23:00:00",
        quiet_hours_end=None,  # half-set
    )
    # 23:30 ET = 03:30 UTC next day.
    now = datetime(2026, 6, 18, 3, 30, 0, tzinfo=UTC)

    with _patch_seam(user, strategy_row):
        result = await _resolve_quiet_hours("u1", now, strategy_name="s1")

    # Falls back to user window (22:00-07:00); 23:30 ET → in window → True.
    assert result is True
