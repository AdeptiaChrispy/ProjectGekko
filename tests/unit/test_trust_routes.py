"""Trust dashboard routes — TRUST-01 / TRUST-06 (Wave-0 RED stub, Plan 05-01).

Asserts the contract for the trust promote/demote/capital routes landed by a
later wave's edit to ``gekko.dashboard.routes``:

  * promote-to-auto re-checks eligibility server-side and returns the
    blocked-explanation block when ineligible (SC-5 / D-T18b) — NEVER silent.
  * demote-from-auto is one-click.
  * capital increase requires a typed-name confirm (typed-confirm pattern).

EXPECTED RED until the routes land. The import-time symbol probe gates
collection so these fail until the route functions exist.
"""

from __future__ import annotations

import pytest

# RED until later wave wires the trust routes into the dashboard router.
trust_routes = pytest.importorskip(
    "gekko.dashboard.routes",
    reason="dashboard.routes exists; the trust route functions are added later",
)


@pytest.mark.asyncio
async def test_promote_to_auto_rechecks_eligibility_server_side() -> None:
    """Ineligible strategy + forged promote POST → blocked-explanation, no promote."""
    assert hasattr(trust_routes, "promote_to_auto"), (
        "promote_to_auto route not yet implemented (later Plan-05 wave)"
    )


@pytest.mark.asyncio
async def test_demote_from_auto_is_one_click() -> None:
    """Demote requires no typed confirm — one POST flips trust to propose-only."""
    assert hasattr(trust_routes, "demote_from_auto"), (
        "demote_from_auto route not yet implemented (later Plan-05 wave)"
    )


@pytest.mark.asyncio
async def test_capital_increase_requires_typed_confirm() -> None:
    """Raising capital_ceiling_usd requires a typed-strategy-name confirm."""
    assert hasattr(trust_routes, "set_capital_ceiling_route") or hasattr(
        trust_routes, "strategy_capital"
    ), "capital route not yet implemented (later Plan-05 wave)"
