"""Wave 0 stub — populated in Plan 03-03.

Tests for Slack chat.update of expired proposal card (HITL-03).
Uses respx mock of chat.update Slack API call.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_chat_update_expired_card() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")


@pytest.mark.asyncio
async def test_chat_update_missing_ts_falls_back_to_dm() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-03")
