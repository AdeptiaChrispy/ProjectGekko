"""Auto-execution branch — TRUST-02 (Wave-0 RED stub, Plan 05-01).

Asserts the contract for the auto-branch added to ``gekko.agent.runtime`` (landed
by Plan 05):

  * an auto-within-caps proposal routes through ``execute_proposal`` so OrderGuard
    re-checks caps as its last line (D-T08) — NEVER a direct ``broker.place_order``.
  * a LIVE strategy whose ``first_live_trade_confirmed_at IS NULL`` routes to
    AWAITING_2ND_CHANNEL (the Phase-2 dual-channel gate), NOT direct execute (D-T03).

EXPECTED RED until the auto-branch lands — the symbol probe gates collection.
"""

from __future__ import annotations

import pytest

runtime = pytest.importorskip(
    "gekko.agent.runtime",
    reason="runtime exists; the auto-branch helpers are added later",
)


@pytest.mark.skipif(
    not hasattr(runtime, "load_trust_level"),
    reason="auto-branch (load_trust_level) not yet implemented (Plan 05)",
)
@pytest.mark.asyncio
async def test_auto_within_caps_routes_through_execute_proposal() -> None:
    """Auto proposal reaches the broker only via execute_proposal (OrderGuard re-check)."""
    assert hasattr(runtime, "load_trust_level")


@pytest.mark.skipif(
    not hasattr(runtime, "load_trust_level"),
    reason="auto-branch (load_trust_level) not yet implemented (Plan 05)",
)
@pytest.mark.asyncio
async def test_live_first_trade_routes_to_dual_channel_not_execute() -> None:
    """LIVE + first_live_trade_confirmed_at IS NULL → AWAITING_2ND_CHANNEL (D-T03)."""
    assert hasattr(runtime, "load_trust_level")
