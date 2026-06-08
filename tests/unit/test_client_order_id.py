"""Deterministic ``client_order_id`` — Plan 01-05 Task 1.

Tests for ``gekko.core.ids.compute_client_order_id``. Per CONTEXT.md D-20:

    client_order_id = sha256(f"{strategy_id}|{decision_id}|{side}|{qty}|{ticker}")[:32]

The function MUST be:

1. **Deterministic** — same inputs produce the same id, always (Pitfall 4 /
   Knight Capital prevention).
2. **Differentiating** — different ``decision_id`` produces a different id so
   legitimate re-runs (a new research cycle) get a fresh idempotency key.
3. **Normalization-stable** — ``Decimal("100")`` and ``Decimal("100.0")``
   produce the SAME id; ``side="buy"`` / ``side="BUY"`` produce the same id;
   ``ticker="nvda"`` / ``ticker="NVDA"`` produce the same id.

The 32-char prefix is locked by D-20; if Alpaca's max client_order_id length
ever changes, this is the single point of truth to update.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_returns_32_char_lowercase_hex() -> None:
    """Output is a 32-character lowercase hex string."""
    from gekko.core.ids import compute_client_order_id

    cid = compute_client_order_id(
        strategy_id="s1", decision_id="d1", side="buy", qty=Decimal("100"), ticker="NVDA"
    )
    assert isinstance(cid, str)
    assert len(cid) == 32
    assert cid == cid.lower()
    # all chars are valid hex
    int(cid, 16)


def test_deterministic_same_inputs_same_id() -> None:
    """D-20 / Pitfall 4: same inputs ALWAYS produce the same id."""
    from gekko.core.ids import compute_client_order_id

    args = {
        "strategy_id": "ai-infra",
        "decision_id": "decision-001",
        "side": "buy",
        "qty": Decimal("5"),
        "ticker": "NVDA",
    }
    first = compute_client_order_id(**args)
    second = compute_client_order_id(**args)
    assert first == second


def test_different_decision_id_produces_different_id() -> None:
    """A legitimate re-run (new decision_id) gets a fresh idempotency key.

    Per RESEARCH §Pitfall 4: ``decision_id`` is the per-research-cycle
    nonce — if the Researcher runs the strategy again tomorrow, the new
    decision_id means a fresh client_order_id, so Alpaca treats it as a
    new order (not a duplicate-reject from yesterday's filled order).
    """
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "side": "buy",
        "qty": Decimal("100"),
        "ticker": "NVDA",
    }
    a = compute_client_order_id(decision_id="d1", **base)
    b = compute_client_order_id(decision_id="d2", **base)
    assert a != b


# ---------------------------------------------------------------------------
# Normalization stability
# ---------------------------------------------------------------------------


def test_qty_trailing_zero_normalized() -> None:
    """``Decimal("100")`` and ``Decimal("100.0")`` MUST produce the same id.

    Per RESEARCH §Pattern 4 — "standardize on ``str(qty.normalize())`` if
    fractional shares are ever in play." Plan 01-05 normalizes always.

    The canonical form uses ``format(qty.normalize(), 'f')`` rather than
    plain ``str(qty.normalize())`` — the latter produces "1E+2" for
    ``Decimal("100").normalize()``, which is technically deterministic but
    visually surprising and not what users expect when debugging.
    """
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "side": "buy",
        "ticker": "NVDA",
    }
    a = compute_client_order_id(qty=Decimal("100"), **base)
    b = compute_client_order_id(qty=Decimal("100.0"), **base)
    c = compute_client_order_id(qty=Decimal("100.00"), **base)
    assert a == b == c


def test_side_case_insensitive() -> None:
    """``side="BUY"`` and ``side="buy"`` produce the same id."""
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "qty": Decimal("5"),
        "ticker": "NVDA",
    }
    a = compute_client_order_id(side="buy", **base)
    b = compute_client_order_id(side="BUY", **base)
    c = compute_client_order_id(side="Buy", **base)
    assert a == b == c


def test_ticker_case_insensitive() -> None:
    """``ticker="nvda"`` and ``ticker="NVDA"`` produce the same id."""
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "side": "buy",
        "qty": Decimal("5"),
    }
    a = compute_client_order_id(ticker="NVDA", **base)
    b = compute_client_order_id(ticker="nvda", **base)
    c = compute_client_order_id(ticker="Nvda", **base)
    assert a == b == c


def test_ticker_whitespace_stripped() -> None:
    """A leading/trailing space in ticker doesn't change the id.

    Defensive against agent-side ticker mistakes (``" NVDA"`` vs ``"NVDA"``).
    """
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "side": "buy",
        "qty": Decimal("5"),
    }
    a = compute_client_order_id(ticker="NVDA", **base)
    b = compute_client_order_id(ticker="  NVDA  ", **base)
    assert a == b


# ---------------------------------------------------------------------------
# Side-channel: different field changes => different id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,a_val,b_val",
    [
        ("strategy_id", "ai-infra", "value-yield"),
        ("side", "buy", "sell"),
        ("qty", Decimal("5"), Decimal("10")),
        ("ticker", "NVDA", "AMD"),
    ],
)
def test_changing_any_input_changes_id(field: str, a_val: object, b_val: object) -> None:
    """Any change in any contributing input flips the id (hash sensitivity)."""
    from gekko.core.ids import compute_client_order_id

    base = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "side": "buy",
        "qty": Decimal("5"),
        "ticker": "NVDA",
    }
    overrides_a = {**base, field: a_val}
    overrides_b = {**base, field: b_val}
    a = compute_client_order_id(**overrides_a)  # type: ignore[arg-type]
    b = compute_client_order_id(**overrides_b)  # type: ignore[arg-type]
    assert a != b
