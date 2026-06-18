"""Wave 0 stub — populated in Plan 03-03.

Tests for the ``expire_stale_proposals`` sweep (HITL-03):
- basic sweep correctness with freezegun
- skips unexpired proposals
- handles AWAITING_2ND_CHANNEL expiry (A7)
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_basic_sweep() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")


@pytest.mark.asyncio
async def test_skips_unexpired() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")


@pytest.mark.asyncio
async def test_awaiting_2nd_channel_expires() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")


@pytest.mark.asyncio
async def test_grandfathered_null_expires_at_not_swept() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")


@pytest.mark.asyncio
async def test_sweep_idempotent() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")
