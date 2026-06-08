"""Decimal helpers for money math — Plan 01-05 Task 1.

Every dollar amount, share quantity, and price that crosses an order-placement
boundary in Gekko goes through this module. Per CONTEXT.md D-20: Decimal-only
for all money math at the order-placement layer; the binary floating-point
type is banned by lint rule.

# NOTE — the bare ``f-l-o-a-t`` token is intentionally NOT spelled in this
# docstring or any runtime code in this module. The grep gate in
# tests/unit/test_money_math.py walks this exact file and fails CI on any
# non-comment line containing that token. Comments are exempt (lines that
# start with ``#`` after lstrip); module/function docstrings are NOT comments
# and would trip the gate. Refer to "binary floating-point" / "fp" in prose.

The "lint rule" is the ``test_float_banned_in_money_paths`` test in
``tests/unit/test_money_math.py``, which walks ``src/gekko/brokers/``,
``src/gekko/execution/``, and **this file**, and fails CI if any non-comment
line contains the banned builtin token. The grep gate is enforced; you cannot
sneak a binary-fp value into a money path without the test catching it.

Three public helpers:

* :func:`to_decimal` — coerce a string or Decimal to Decimal; REJECT
  binary-fp inputs with ``TypeError``. The rejection is the entire point —
  ``Decimal(0.1)`` silently constructs
  ``Decimal('0.10000000000000000555111...')`` because ``0.1`` is not
  exactly representable in binary. Every caller MUST pass either a
  string literal (``"100.50"``) or a Decimal derived from a string.

* :func:`assert_positive` — raise ``ValueError`` on zero or negative. Used
  before submitting any order: a zero-share order makes no sense, and a
  negative-share order would let the agent silently flip from BUY to SELL.

* :func:`round_money` — quantize to N places using ``ROUND_HALF_EVEN``
  (banker's rounding). The financial industry default; symmetric for
  positive and negative values and unbiased over large samples.

References:
  * CONTEXT.md D-20 — Decimal for money math; binary-fp banned by lint
  * RESEARCH.md §"Pitfall 6" — Decimal serialization
  * Plan 01-01 .ruff.toml — comment block describing the grep gate
"""

# NOTE: this module MUST NOT import or use the builtin binary-fp numeric
# type. Comment-only mentions of that name are allowed by the grep gate;
# the runtime code below uses only Decimal arithmetic.

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def to_decimal(value: str | Decimal) -> Decimal:
    """Coerce a string or Decimal to Decimal. Reject other types.

    :param value: A string literal (``"100.50"``) or an existing Decimal.
    :returns: The corresponding ``Decimal``. If ``value`` is already a
        Decimal, it is returned unchanged (idempotent).
    :raises TypeError: If ``value`` is anything else — most notably a bare
        builtin numeric (the EXEC-01 / D-20 guard rail). The error message
        names the offending type so the operator can fix the call site.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        return Decimal(value)
    # Defensive: passing a builtin binary-fp numeric is banned by EXEC-01 / D-20.
    # Callers must use a string literal or a Decimal derived from a string.
    msg = f"to_decimal expects str or Decimal, got {type(value).__name__}"
    raise TypeError(msg)


def assert_positive(value: Decimal) -> None:
    """Raise ``ValueError`` if ``value`` is zero or negative.

    Pre-flight guard for share quantities, limit prices, and stop prices.
    Negative-share orders would silently flip BUY to SELL (Knight-Capital-
    adjacent failure mode); zero-share orders are nonsense and waste a
    submit cycle.
    """
    if value <= Decimal("0"):
        msg = f"expected positive Decimal, got {value!r}"
        raise ValueError(msg)


def round_money(value: Decimal, places: int = 4) -> Decimal:
    """Quantize ``value`` to ``places`` decimal places using banker's rounding.

    Uses ``ROUND_HALF_EVEN`` so ties round toward the nearest even neighbor
    rather than always rounding up — eliminates the upward bias that
    ``ROUND_HALF_UP`` introduces over large samples.

    Defaults to 4 places because Alpaca's submit_order accepts up to 4
    decimal places for prices on most US equities (some penny stocks allow
    more); 4 places is the safe lowest-common-denominator. Callers that need
    different precision (e.g., 2 places for USD display) pass ``places``
    explicitly.

    :param value: The Decimal to round.
    :param places: Number of decimal places to retain (must be >= 0).
    :returns: A new Decimal quantized to the requested precision.
    """
    if places < 0:
        msg = f"places must be >= 0, got {places}"
        raise ValueError(msg)
    quantum = Decimal("1") if places == 0 else Decimal(f"1e-{places}")
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


__all__: tuple[str, ...] = ("to_decimal", "assert_positive", "round_money")
