"""Alpaca retry-decorator gate — plan 02-02 (place_order zero-decorator half).

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land
# (plan 02-02 lands the place_order zero-decorator assertion early because
#  it's the Pitfall 4 / EXEC-03 / BLOCKER #4 Knight-Capital invariant; plan
#  02-03 will replace the module-level skip and add the tenacity-on-GETs
#  assertions when that plan ships the retry decorator.)

Covers EXEC-08 (rate-limit backoff — owned by plan 02-03) AND the EXEC-03
BLOCKER #4 invariant: tenacity decorates only Alpaca GET endpoints
(get_account, get_positions, list_orders, get_asset) — NEVER place_order
POST. The grep gate parses the AlpacaBroker module via ``ast`` and asserts
``place_order`` has zero decorators.

The place_order assertion is exercised TODAY (plan 02-02) via an
``ast``-walking unit test that does NOT depend on tenacity being wired —
it just inspects the source file. The GET-decoration assertions stay
behind the module-level skip until plan 02-03 lands the retry wrapper.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_alpaca_place_order_has_zero_decorators() -> None:
    """Source-bytes assertion that AlpacaBroker.place_order has no decorators.

    Pitfall 4 / EXEC-03 / BLOCKER #4 — Knight-Capital insurance. Order POSTs
    must NEVER auto-retry. Adding ``@retry`` here would create exactly the
    Knight-Capital duplicate-submit loop the broker's ``_is_duplicate_error``
    handler exists to defend against.
    """
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "place_order"
        ):
            # Walk only the AlpacaBroker class's place_order, not the ABC's.
            # The ABC's `place_order` is a stub on Brokerage (separate file).
            found = True
            assert node.decorator_list == [], (
                "AlpacaBroker.place_order has unexpected decorators "
                "(Pitfall 4 / EXEC-03 / BLOCKER #4 — Knight-Capital "
                "invariant): "
                f"{[ast.dump(d) for d in node.decorator_list]!r}"
            )
    assert found, "place_order not found in AlpacaBroker source"


def test_orderguard_place_order_has_zero_decorators() -> None:
    """Same invariant applied to OrderGuard.place_order (plan 02-02 surface)."""
    import gekko.execution.orderguard as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "place_order"
        ):
            found = True
            assert node.decorator_list == [], (
                "OrderGuard.place_order has unexpected decorators "
                "(Pitfall 4 / EXEC-03 — Knight-Capital invariant): "
                f"{[ast.dump(d) for d in node.decorator_list]!r}"
            )
    assert found, "place_order not found in OrderGuard source"


# ---------------------------------------------------------------------------
# Plan 02-03 territory — keep the rest of the test file behind a function-
# level skip rather than the previous module-level skip so the
# zero-decorator assertions above run today.
# ---------------------------------------------------------------------------


import pytest  # noqa: E402  (module-level skip lifted; per-test skips below)


@pytest.mark.skip(reason="Wave-0 stub — plan 02-03 lands tenacity GETs")
def test_alpaca_get_endpoints_decorated_with_tenacity_placeholder() -> None:
    """Will assert tenacity.retry wraps get_account / get_positions / etc.

    Owned by plan 02-03.
    """
