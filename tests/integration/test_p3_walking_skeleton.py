"""Wave 0 stub — populated in Plan 03-07.

Full Phase-3 walking-skeleton integration test covering:
- Proposal with expires_at
- slack_action_dedup INSERT
- approval, order_submitted, fill, daily_pnl audit events
- EXPIRY chain: PENDING -> EXPIRED + chat.update + DM
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_p3_walking_skeleton_approval_chain() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-07")


@pytest.mark.asyncio
async def test_p3_walking_skeleton_expiry_chain() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-07")
