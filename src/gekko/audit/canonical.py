"""Canonical JSON serialization for the audit hash chain — Plan 01-04 Task 1.

The SHA-256 hash chain (D-16) requires byte-stable serialization of the
canonical-subset ``{event_type, payload, ts, user_id}`` so that the same
logical event always produces the same hash regardless of dict-insertion
order, locale, or platform.

Per RESEARCH §"Audit log SHA-256 hash chain" / §"Pattern 3 lock-in", this
module is the **one-shot** architectural lock: the exact JSON shape that
goes into every ``payload_json`` cell in the ``events`` table is whatever
``canonical_json`` produces. Changing the serialization is a backward-
incompatible migration that invalidates every existing chain — so the
implementation is intentionally tiny and explicit.

Design choices:

* ``sort_keys=True`` — deterministic ordering at every dict level.
* ``separators=(",", ":")`` — strips every space and newline from output.
* ``ensure_ascii=True`` — escapes non-ASCII characters to ``\\uXXXX`` so
  the output is byte-stable regardless of the writer's locale. Critical
  because the chain hash is computed over the UTF-8 byte sequence.
* ``default=str`` — falls through to ``__str__`` for non-JSON-native
  values (``Decimal``, ``datetime``, ``UUID``, ``Path``, etc.). Keeps
  the function tolerant of whatever the agent / executor passes in.

**Caller contract — Decimal normalization is the CALLER's job** (RESEARCH
§Pitfall 6). ``Decimal("1.230")`` and ``Decimal("1.23")`` are numerically
equal but their ``__str__`` representations differ, which means
``canonical_json`` would hash them differently. The :func:`normalize_decimals`
helper exists exactly for this — callers that handle money MUST pass their
payload through it first.

References:
  * CONTEXT.md D-14 — single events table + JSON payload
  * CONTEXT.md D-15 — full structured rationale in payload
  * CONTEXT.md D-16 — SHA-256 hash chain over canonical subset
  * CONTEXT.md Claude's Discretion A11 — genesis ``prev_hash`` is ``"0" * 64``
  * RESEARCH.md §"Pattern 3" — canonical-subset shape lock-in
  * RESEARCH.md §"Pitfall 6" — Decimal normalization gotcha
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Genesis ``prev_hash`` for the first event of any user's chain.
#:
#: Locked per CONTEXT.md Claude's Discretion item A11: the convention is the
#: 64-character all-zero string (matching the hex digest width of SHA-256).
#: ``walk_chain`` seeds ``expected_prev`` with this value when iterating a
#: user's events.
GENESIS_PREV_HASH: str = "0" * 64


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


def canonical_json(payload: Any) -> str:
    """Serialize ``payload`` to a byte-stable canonical JSON string.

    Output guarantees:

    * Keys sorted lexicographically at every dict level.
    * No whitespace — ``separators=(",", ":")``.
    * ASCII-only — every non-ASCII codepoint is escaped to ``\\uXXXX``.
    * Non-JSON-native values (``Decimal``, ``datetime``, ``UUID``) fall
      through to ``str(value)`` via ``default=str``.

    .. important::

       This function does **not** normalize ``Decimal`` values. Two
       numerically-equal Decimals (``Decimal("1.230")`` vs ``Decimal("1.23")``)
       serialize to different strings here, which would break the hash chain
       if a caller treats them as equivalent. Callers that handle money MUST
       pass the payload through :func:`normalize_decimals` first.

    .. important::

       ``datetime`` values fall through to ``default=str``, which produces the
       ``"2026-06-08 15:00:00+00:00"`` form (space separator). Callers should
       call ``dt.isoformat()`` themselves to get the more standard
       ``"2026-06-08T15:00:00+00:00"`` form — both forms are tolerated, but
       only one ends up in the chain.

    :param payload: A JSON-serializable structure (or one containing
        ``Decimal`` / ``datetime`` / other objects supporting ``__str__``).
    :returns: The canonical JSON string.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


# ---------------------------------------------------------------------------
# Decimal normalization
# ---------------------------------------------------------------------------


def normalize_decimals(payload: Any) -> Any:
    """Recursively normalize every ``Decimal`` in ``payload``.

    ``Decimal.normalize()`` strips trailing zeros, so ``Decimal("1.230")``
    and ``Decimal("1.23")`` both collapse to ``Decimal("1.23")`` — which
    means :func:`canonical_json` now produces the same string for both.

    Walks ``dict``, ``list``, and ``tuple`` containers recursively. Other
    values (including non-Decimal numerics) are returned unchanged.

    The input is **not mutated** — a fresh structure is returned, so
    callers can keep their original payload for non-audit purposes (e.g.,
    pretty-printing in Slack with original precision intact).

    Edge case (per Python docs): ``Decimal("0").normalize()`` yields
    ``Decimal("0E+0")`` rather than ``Decimal("0")``. We call ``+x`` on
    the result to coerce zero back to its plain form so the canonical
    representation is the human-readable one.

    :param payload: Arbitrary nested structure (dict / list / tuple /
        Decimal / anything else).
    :returns: A new structure with the same shape, with every ``Decimal``
        replaced by its normalized form.
    """
    if isinstance(payload, dict):
        return {k: normalize_decimals(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [normalize_decimals(v) for v in payload]
    if isinstance(payload, tuple):
        return tuple(normalize_decimals(v) for v in payload)
    if isinstance(payload, Decimal):
        # ``+normalized`` collapses ``0E+0`` back to ``0`` (current context
        # default) while leaving other values like ``Decimal("1.23")``
        # unchanged. See Python docs: Decimal.normalize -> "result has no
        # trailing zeros; an exception is that 0 is given as 0E+0".
        return +payload.normalize()
    return payload


__all__: tuple[str, ...] = (
    "GENESIS_PREV_HASH",
    "canonical_json",
    "normalize_decimals",
)
