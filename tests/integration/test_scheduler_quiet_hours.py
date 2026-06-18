"""Wave 0 stub — populated in Plan 03-03.

Integration test: APScheduler trigger_strategy_run skipped in quiet-hours window (HITL-05).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_scheduler_skips_in_quiet_hours() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")
