"""Wave-0 stub — HITL-06 dual-channel first-live-trade gate.

# WAVE-0 STUB: owned by plan 02-06 — DO NOT delete the skip until that plan's tasks land

Covers HITL-06 — the first live trade for a (user, strategy) pair requires
approval in BOTH Slack DM AND the dashboard before the executor proceeds.
Slack-only OR dashboard-only is NOT sufficient. Subsequent live trades
revert to single-channel approval.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_first_live_requires_dual_channel_placeholder() -> None:
    """Will assert proposal stays AWAITING_2ND_CHANNEL until both Slack+dashboard ack."""
    pass
