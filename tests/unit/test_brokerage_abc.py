"""Brokerage ABC contract — Plan 01-05 Task 2.

Tests for ``gekko.brokers.base``:

* ``Brokerage`` is an ABC and cannot be instantiated directly.
* A subclass missing any abstract method cannot be instantiated.
* ``OrderRequest`` and ``OrderResult`` are frozen dataclasses.
* The file contains explicit ``Phase 2 hook``, ``Phase 8 extension``, and
  ``Phase 9 extension`` markers (per the load-bearing-interface contract
  RESEARCH §Architecture Patterns calls out).

The seven abstract methods every concrete broker MUST implement:
``health_check, get_account, get_positions, get_quote, place_order,
get_order_by_client_order_id, cancel_order``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal concrete subclass for instantiability tests
# ---------------------------------------------------------------------------


def _make_dummy_broker_class() -> type:
    """Return a concrete ``Brokerage`` subclass implementing every abstract method."""
    from gekko.brokers.base import Brokerage, OrderRequest, OrderResult

    class _DummyBroker(Brokerage):
        name = "dummy"
        supports_fractional = True
        is_paper = True

        async def health_check(self) -> bool:
            return True

        async def get_account(self) -> dict[str, Any]:
            return {}

        async def get_positions(self) -> list[dict[str, Any]]:
            return []

        async def get_quote(self, symbol: str) -> dict[str, Any]:
            return {"symbol": symbol}

        async def place_order(self, req: OrderRequest) -> OrderResult:
            return OrderResult(
                broker_order_id="bo-1",
                client_order_id=req.client_order_id,
                status="accepted",
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                raw={},
            )

        async def get_order_by_client_order_id(self, client_order_id: str) -> OrderResult | None:
            return None

        async def cancel_order(self, broker_order_id: str) -> bool:
            return True

    return _DummyBroker


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_brokerage_cannot_be_instantiated_directly() -> None:
    """``Brokerage`` itself is an ABC — direct construction raises TypeError."""
    from gekko.brokers.base import Brokerage

    with pytest.raises(TypeError):
        Brokerage()  # type: ignore[abstract]


def test_incomplete_subclass_cannot_be_instantiated() -> None:
    """A subclass that omits any abstract method is still abstract."""
    from gekko.brokers.base import Brokerage

    class _IncompleteBroker(Brokerage):  # missing every abstract method
        pass

    with pytest.raises(TypeError):
        _IncompleteBroker()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated() -> None:
    """A subclass implementing every abstract method instantiates cleanly."""
    cls = _make_dummy_broker_class()
    instance = cls()
    assert instance.name == "dummy"
    assert instance.supports_fractional is True
    assert instance.is_paper is True


# ---------------------------------------------------------------------------
# OrderRequest / OrderResult dataclasses
# ---------------------------------------------------------------------------


def test_order_request_constructs_and_is_frozen() -> None:
    """OrderRequest is a frozen dataclass — fields cannot be reassigned."""
    from gekko.brokers.base import OrderRequest
    from gekko.core.types import OrderSide, OrderType

    req = OrderRequest(
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=Decimal("5"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1234.56"),
    )
    assert req.symbol == "NVDA"
    assert req.side is OrderSide.BUY
    assert req.qty == Decimal("5")
    assert req.order_type is OrderType.LIMIT
    assert req.limit_price == Decimal("1234.56")
    # Frozen: reassignment raises.
    with pytest.raises(FrozenInstanceError):
        req.symbol = "AAPL"  # type: ignore[misc]


def test_order_result_constructs_and_is_frozen() -> None:
    """OrderResult is a frozen dataclass."""
    from gekko.brokers.base import OrderResult

    result = OrderResult(
        broker_order_id="x",
        client_order_id="y",
        status="filled",
        filled_qty=Decimal("5"),
        avg_fill_price=Decimal("1234.56"),
        raw={"id": "x"},
    )
    assert result.broker_order_id == "x"
    assert result.filled_qty == Decimal("5")
    with pytest.raises(FrozenInstanceError):
        result.status = "rejected"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Phase-extension comment markers (load-bearing-interface contract)
# ---------------------------------------------------------------------------


def test_base_module_documents_future_phase_extensions() -> None:
    """``base.py`` MUST mention P2 / P8 / P9 extension hooks.

    Per SKELETON.md and RESEARCH §Architecture Patterns: the Brokerage
    ABC is the load-bearing interface. Plan 01-05's job is to lock it
    explicitly — the source file should annotate where future plans
    plug in so a reader scanning the file knows the future shape.
    """
    base_py = Path(__file__).resolve().parents[2] / "src" / "gekko" / "brokers" / "base.py"
    text = base_py.read_text(encoding="utf-8")
    assert "Phase 2" in text, "base.py must reference Phase 2 OrderGuard hook"
    assert "Phase 8" in text, "base.py must reference Phase 8 IBKR/Schwab extension"
    assert "Phase 9" in text, "base.py must reference Phase 9 browser-fallback extension"
