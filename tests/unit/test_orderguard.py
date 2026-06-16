"""Wave-0 stub — OrderGuard unit tests.

# WAVE-0 STUB: owned by plan 02-02 (universe + caps + qty×price) and plan 02-03 (PDT + T+1 + wash-sale FLAG) — DO NOT delete the skip until those plans' tasks land

Covers EXEC-04 (universe + hard caps + qty×price drift), EXEC-11 (PDT 4th
round-trip + T+1 unsettled), EXEC-09 (wash-sale FLAG path). Also hosts the
place_order grep-gate test asserting `place_order` is NOT decorated with
tenacity (EXEC-03 / BLOCKER #4 / Pitfall 4 — POSTs never blind-retry).

When plan 02-02 lands the OrderGuard universe/caps/qty×price assertions, the
module-level pytest.skip is removed and per-test skips (if any) gate the
remaining unimplemented cases until plan 02-03 finishes the PDT/T+1/wash-sale
half.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_orderguard_universe_violation_placeholder() -> None:
    """Placeholder — plan 02-02 implements OrderGuard universe check.

    Will assert that proposing a ticker outside the strategy's watchlist
    raises OrderGuardRejected(reject_code='universe').
    """
    pass


def test_orderguard_place_order_zero_decorators_placeholder() -> None:
    """Placeholder — EXEC-03 grep gate.

    Will assert that AlpacaBroker.place_order has zero decorators (no
    tenacity retry on POSTs per Pitfall 4 / BLOCKER #4).
    """
    pass
