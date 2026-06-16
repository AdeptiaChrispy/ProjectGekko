"""Wave-0 stub — Alpaca tenacity retry on GETs only + place_order grep gate.

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-08 (rate-limit backoff) AND BLOCKER #4 / EXEC-03 invariant:
tenacity decorates only Alpaca GET endpoints (get_account, get_positions,
list_orders, get_asset) — NEVER place_order POST. The grep gate parses the
AlpacaBroker module via `ast` and asserts `place_order` has zero decorators.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_alpaca_get_endpoints_decorated_with_tenacity_placeholder() -> None:
    """Will assert tenacity.retry wraps get_account / get_positions / etc."""
    pass


def test_alpaca_place_order_has_zero_decorators_placeholder() -> None:
    """Will ast-walk and assert place_order.decorator_list == []."""
    pass
