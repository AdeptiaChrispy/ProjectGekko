"""Wave 0 stub — populated in Plan 03-06.

Tests that MarketClosed and BrokerOrderError paths emit Slack DMs (REPT-01).
Covers executor lines for both error branches.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_market_closed_dm_sent() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-06")


@pytest.mark.asyncio
async def test_broker_order_error_dm_sent() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-06")
