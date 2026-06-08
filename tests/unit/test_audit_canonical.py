"""Plan 01-04 Task 1 — canonical JSON for the audit hash chain.

Behaviors 1-8 from PLAN.md `<feature>`:

1. Sorted keys, no whitespace, ASCII-safe.
2. Decimal -> str via default.
3. Decimal normalization is the CALLER's job (canonical_json itself does NOT
   normalize — that is what ``normalize_decimals`` exists for).
4. ``normalize_decimals`` helper recursively normalizes every Decimal value in
   a nested dict/list/tuple.
5. Nested dicts/lists preserved (sorted at every dict level).
6. Genesis constant ``GENESIS_PREV_HASH == "0" * 64``.
7. Non-ASCII handled (ensure_ascii=True escapes).
8. datetime serialized via default=str (callers should ISO-format BEFORE
   passing to canonical_json so the more-standard 2026-06-08T15:00:00+00:00
   form is what ends up in the chain).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gekko.audit.canonical import (
    GENESIS_PREV_HASH,
    canonical_json,
    normalize_decimals,
)

# ---------------------------------------------------------------------------
# Behavior 1 — sorted keys, no whitespace, ASCII-safe
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_strips_whitespace() -> None:
    """``canonical_json`` produces ``{"a":2,"b":1}`` from ``{"b":1,"a":2}``."""
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_no_trailing_newline() -> None:
    """No trailing newline / no whitespace anywhere."""
    out = canonical_json({"x": 1})
    assert "\n" not in out
    assert " " not in out
    assert out == '{"x":1}'


# ---------------------------------------------------------------------------
# Behavior 2 — Decimal -> str via default
# ---------------------------------------------------------------------------


def test_canonical_json_decimal_serialized_as_string() -> None:
    """Decimal values serialize as JSON strings via ``default=str``."""
    assert canonical_json({"qty": Decimal("100")}) == '{"qty":"100"}'


# ---------------------------------------------------------------------------
# Behavior 3 — canonical_json does NOT auto-normalize Decimals
# ---------------------------------------------------------------------------


def test_canonical_json_does_not_normalize_decimal_trailing_zeros() -> None:
    """The function preserves the original Decimal's representation.

    Per RESEARCH §Pitfall 6, normalization is the CALLER's responsibility —
    ``canonical_json`` itself MUST NOT strip trailing zeros, because that
    would silently mutate caller-visible payload shapes.
    """
    assert canonical_json({"qty": Decimal("1.230")}) == '{"qty":"1.230"}'
    # Not equal to the "1.23" form — the strings differ even though the
    # numeric value is the same. This is exactly the bug normalize_decimals
    # is there to prevent.
    assert canonical_json({"qty": Decimal("1.230")}) != canonical_json(
        {"qty": Decimal("1.23")}
    )


# ---------------------------------------------------------------------------
# Behavior 4 — normalize_decimals helper
# ---------------------------------------------------------------------------


def test_normalize_decimals_strips_trailing_zeros() -> None:
    """After ``normalize_decimals``, "1.230" and "1.23" produce the same JSON."""
    a = normalize_decimals({"qty": Decimal("1.230")})
    b = normalize_decimals({"qty": Decimal("1.23")})
    assert canonical_json(a) == canonical_json(b)


def test_normalize_decimals_walks_nested_dicts() -> None:
    """Nested dicts are walked recursively."""
    out = normalize_decimals(
        {"outer": {"inner": Decimal("2.500")}, "scalar": 1}
    )
    assert canonical_json(out) == '{"outer":{"inner":"2.5"},"scalar":1}'


def test_normalize_decimals_walks_lists() -> None:
    """Lists are walked recursively."""
    out = normalize_decimals({"prices": [Decimal("1.10"), Decimal("2.00")]})
    assert canonical_json(out) == '{"prices":["1.1","2"]}'


def test_normalize_decimals_does_not_mutate_input() -> None:
    """Input dict is not mutated — a fresh dict is returned."""
    original = {"qty": Decimal("1.230")}
    snapshot_id = id(original)
    out = normalize_decimals(original)
    assert id(out) != snapshot_id
    # Original still has the un-normalized Decimal.
    assert original["qty"] == Decimal("1.230")
    assert original["qty"].as_tuple().exponent == -3
    # Returned dict has the normalized Decimal (exponent shifted).
    assert out["qty"] == Decimal("1.23")


# ---------------------------------------------------------------------------
# Behavior 5 — nested dicts/lists preserved + sorted at every level
# ---------------------------------------------------------------------------


def test_canonical_json_nested_dict_sorted_at_every_level() -> None:
    """Sub-objects are sorted recursively; lists preserve element order."""
    payload = {"a": {"y": 2, "x": 1}, "b": [3, 1, 2]}
    # Outer keys a/b sorted; inner keys x/y sorted; list order [3,1,2] preserved.
    assert canonical_json(payload) == '{"a":{"x":1,"y":2},"b":[3,1,2]}'


def test_canonical_json_no_whitespace_in_nested_structure() -> None:
    """No whitespace anywhere in nested output."""
    out = canonical_json({"a": {"b": [1, 2, 3]}})
    assert out == '{"a":{"b":[1,2,3]}}'


# ---------------------------------------------------------------------------
# Behavior 6 — GENESIS_PREV_HASH constant
# ---------------------------------------------------------------------------


def test_genesis_prev_hash_is_sixty_four_zeroes() -> None:
    """Locked per A11 / CONTEXT.md Claude's Discretion: ``"0" * 64``."""
    assert GENESIS_PREV_HASH == "0" * 64
    assert len(GENESIS_PREV_HASH) == 64
    assert set(GENESIS_PREV_HASH) == {"0"}


# ---------------------------------------------------------------------------
# Behavior 7 — non-ASCII -> escaped (byte-stable across locales)
# ---------------------------------------------------------------------------


def test_canonical_json_escapes_non_ascii() -> None:
    """Non-ASCII characters are escaped to \\uXXXX so hashes are locale-stable."""
    out = canonical_json({"text": "café"})
    # ASCII-only output
    assert all(ord(c) < 128 for c in out)
    # Specifically: 'é' is U+00E9 → "é"
    assert "\\u00e9" in out


# ---------------------------------------------------------------------------
# Behavior 8 — datetime via default=str
# ---------------------------------------------------------------------------


def test_canonical_json_serializes_datetime_via_default_str() -> None:
    """``datetime`` falls through to ``default=str`` → ``str(dt)``."""
    dt = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)
    # str(dt) == "2026-06-08 15:00:00+00:00" (space separator)
    out = canonical_json({"ts": dt})
    assert out == '{"ts":"2026-06-08 15:00:00+00:00"}'


def test_canonical_json_serializes_iso_format_datetime() -> None:
    """If the caller passes ``dt.isoformat()`` first the standard T-separator
    is preserved."""
    dt = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)
    out = canonical_json({"ts": dt.isoformat()})
    assert out == '{"ts":"2026-06-08T15:00:00+00:00"}'


# ---------------------------------------------------------------------------
# Determinism — same input twice -> same output (sanity contract)
# ---------------------------------------------------------------------------


def test_canonical_json_is_deterministic_across_dict_insertion_order() -> None:
    """The whole point: insertion order doesn't change the output."""
    a = {"x": 1, "y": 2, "z": 3}
    b = {"z": 3, "y": 2, "x": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_different_content_produces_different_output() -> None:
    """Same shape, different values → different canonical output."""
    assert canonical_json({"x": 1}) != canonical_json({"x": 2})


# ---------------------------------------------------------------------------
# Integration sanity — combining normalize_decimals + canonical_json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lhs,rhs",
    [
        (Decimal("100"), Decimal("100.00")),
        (Decimal("1.5"), Decimal("1.50")),
        (Decimal("0"), Decimal("0.0")),
    ],
)
def test_normalize_decimals_makes_equal_values_canonically_equal(
    lhs: Decimal, rhs: Decimal
) -> None:
    """Numerically equal Decimals produce the same canonical JSON after normalize."""
    a = normalize_decimals({"v": lhs})
    b = normalize_decimals({"v": rhs})
    assert canonical_json(a) == canonical_json(b)
