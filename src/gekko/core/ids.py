"""Deterministic ``client_order_id`` — Plan 01-05 Task 1.

Per CONTEXT.md D-20:

    client_order_id = sha256(f"{strategy_id}|{decision_id}|{side}|{qty}|{ticker}")[:32]

This is the **load-bearing idempotency key** for every order Gekko submits.
Same inputs always produce the same id. Two consequences:

1. **Knight Capital prevention (Pitfall 4).** If the broker submit POST
   fails partway (timeout, 5xx, network blip), the retry policy is "query
   the existing order by client_order_id; do NOT re-POST." Alpaca rejects
   a duplicate client_order_id with HTTP 422 — the rejection IS the
   safety net, and querying-then-returning the existing order is the
   correct response.

2. **Legitimate re-runs get fresh ids.** Each research cycle's
   ``decision_id`` is a fresh nonce (typically a UUIDv4 generated in
   Plan 01-07's Decision agent), so tomorrow's "buy NVDA" research run
   produces a different client_order_id than yesterday's. The broker
   treats them as separate orders.

Normalization rules (must match across every caller of the function):

* ``side`` is lowercased.
* ``ticker`` is uppercased and whitespace-stripped.
* ``qty`` is canonicalized via ``format(qty.normalize(), 'f')`` so
  ``Decimal("100")`` and ``Decimal("100.0")`` produce the same id. Using
  ``str(qty.normalize())`` directly would yield ``"1E+2"`` for the same
  inputs — technically deterministic, but visually surprising and not
  what humans expect in audit logs.

The output is the first 32 characters of the hex digest (``hexdigest()[:32]``).
Alpaca's max client_order_id length is 48 characters as of alpaca-py 0.43;
32 leaves headroom for future prefix scoping (e.g., ``"gekko-"``) without
collision-rate concerns — 32 hex chars = 128 bits of entropy.

References:
  * CONTEXT.md D-20 — exact hashing scheme
  * RESEARCH.md §"Pattern 4 — Deterministic client_order_id"
  * RESEARCH.md §"Pitfall 4" — never re-POST on duplicate-rejection
"""

from __future__ import annotations

import hashlib
from decimal import Decimal


def compute_client_order_id(
    *,
    strategy_id: str,
    decision_id: str,
    side: str,
    qty: Decimal,
    ticker: str,
) -> str:
    """Compute the deterministic ``client_order_id`` for an order.

    Inputs are keyword-only (no positional API) so call sites are unambiguous
    even when the parameter order changes in future revisions.

    :param strategy_id: The Strategy.strategy_id this order belongs to.
        Stable across re-runs of the same strategy.
    :param decision_id: Per-research-cycle nonce. A new value here means
        "a new research run reached a new conclusion" — even if the other
        inputs match yesterday's order, this differentiator produces a
        fresh client_order_id, so Alpaca accepts the new order rather than
        rejecting it as a duplicate of yesterday's.
    :param side: "buy" or "sell" (case-insensitive — normalized to lower).
    :param qty: Share quantity as a Decimal. Trailing-zero variants
        (``Decimal("100")`` vs ``Decimal("100.0")``) collapse to the same
        canonical form via ``format(qty.normalize(), 'f')``.
    :param ticker: Equity ticker (case-insensitive, whitespace-stripped).
    :returns: A 32-character lowercase hex string suitable for direct use
        as Alpaca's ``client_order_id`` parameter.
    """
    normalized_side = side.lower()
    normalized_ticker = ticker.upper().strip()
    # Canonical qty form: format with 'f' avoids the ``Decimal("100").normalize()
    # -> "1E+2"`` surprise. ``format(d, 'f')`` always produces fixed-point
    # notation: "100", "1.5", "0.001" — never scientific notation.
    normalized_qty = format(qty.normalize(), "f")

    raw = f"{strategy_id}|{decision_id}|{normalized_side}|{normalized_qty}|{normalized_ticker}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


__all__: tuple[str, ...] = ("compute_client_order_id",)
