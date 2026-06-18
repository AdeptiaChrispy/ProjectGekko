"""ProposalWriter expires_at stamping tests — Plan 03-01 Task 3.

Exercises three cases per D-51:
(a) strategy with proposal_timeout_minutes=None -> expires_at = now + 30min
(b) strategy with proposal_timeout_minutes=15 -> expires_at = now + 15min
(c) freezegun-pinned now so the expected ISO string matches

Body filled by Task 3 of this plan.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_default_timeout_thirty_minutes() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")


@pytest.mark.asyncio
async def test_custom_timeout_fifteen_minutes() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")


@pytest.mark.asyncio
async def test_expires_at_iso_matches_frozen_time() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")
