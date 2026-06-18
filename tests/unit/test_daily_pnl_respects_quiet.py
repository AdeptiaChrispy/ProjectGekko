"""Wave 0 stub — populated in Plan 03-06.

Tests that daily P&L digest is deferred when 16:30 ET falls in the quiet window
and fires when outside the quiet window (REPT-01).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_daily_pnl_deferred_in_quiet_window() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-06")


@pytest.mark.asyncio
async def test_daily_pnl_fires_outside_quiet_window() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-06")
