"""Money math + float-ban grep gate — Plan 01-05 Task 1.

Two concerns live in this module:

1. **Behavior tests** for ``gekko.core.money`` — ``to_decimal`` rejects floats,
   ``assert_positive`` raises on zero/negative, ``round_money`` uses banker's
   rounding (ROUND_HALF_EVEN).
2. **The grep gate** — ``test_float_banned_in_money_paths`` walks
   ``src/gekko/brokers/``, ``src/gekko/execution/``, and
   ``src/gekko/core/money.py`` and fails CI if any non-comment line mentions
   the bare token ``float``. This is the EXEC-01 / D-20 enforcement: P1
   never let a ``float`` reach an order-placement codepath, and no later
   plan is allowed to sneak one in.

The grep gate is the Plan 01-01 ``.ruff.toml`` planner-locked enforcement
(see the comment block at the top of ``.ruff.toml``). Ruff itself has no
rule for "ban a builtin name in a path subset" — this test IS the rule.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# to_decimal
# ---------------------------------------------------------------------------


def test_to_decimal_accepts_string() -> None:
    """A string passes through to a Decimal with identical textual value."""
    from gekko.core.money import to_decimal

    assert to_decimal("100.50") == Decimal("100.50")


def test_to_decimal_idempotent_on_decimal() -> None:
    """A Decimal in -> the SAME Decimal out (no re-construction)."""
    from gekko.core.money import to_decimal

    d = Decimal("100.50")
    assert to_decimal(d) == d


def test_to_decimal_rejects_float() -> None:
    """A bare float MUST raise TypeError — this is the EXEC-01 guard.

    Accepting ``float`` here is the entire bug ``Decimal`` math defends
    against: ``Decimal(0.1)`` silently constructs ``Decimal('0.1000000...
    000005551115123...')``. ``to_decimal`` refuses floats so callers are
    forced to pass strings (or Decimals derived from strings).
    """
    from gekko.core.money import to_decimal

    with pytest.raises(TypeError):
        to_decimal(100.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# assert_positive
# ---------------------------------------------------------------------------


def test_assert_positive_rejects_zero() -> None:
    from gekko.core.money import assert_positive

    with pytest.raises(ValueError):
        assert_positive(Decimal("0"))


def test_assert_positive_rejects_negative() -> None:
    from gekko.core.money import assert_positive

    with pytest.raises(ValueError):
        assert_positive(Decimal("-1"))


def test_assert_positive_accepts_small_positive() -> None:
    """The smallest sensible positive value (one basis point) is accepted."""
    from gekko.core.money import assert_positive

    # Should not raise.
    assert_positive(Decimal("0.01"))


# ---------------------------------------------------------------------------
# round_money
# ---------------------------------------------------------------------------


def test_round_money_uses_banker_rounding_to_4_places() -> None:
    """ROUND_HALF_EVEN: 1.234567 -> 1.2346 (round half away from zero on odd)."""
    from gekko.core.money import round_money

    assert round_money(Decimal("1.234567"), places=4) == Decimal("1.2346")


def test_round_money_half_even_tie() -> None:
    """ROUND_HALF_EVEN tie-break: 1.23455 -> 1.2346 (next digit is 5, prior is 5 even -> round to 6)."""
    from gekko.core.money import round_money

    # 1.23455 -> ROUND_HALF_EVEN -> 1.2346 (since 5 is at boundary, round to even neighbor 6)
    # Actually with HALF_EVEN: 1.23455 -> 1.2346 because 5 is the tie-digit and the
    # preceding digit 5 is odd, so we round up to make it even (6). Verify.
    assert round_money(Decimal("1.23455"), places=4) == Decimal("1.2346")


# ---------------------------------------------------------------------------
# Float-ban grep gate — EXEC-01 / D-20 lock-in
# ---------------------------------------------------------------------------


def test_float_banned_in_money_paths() -> None:
    """No ``float`` token in money-handling modules.

    Per EXEC-01 / D-20, ``float`` in money-handling modules is a
    Knight-Capital-class risk. This grep gate is the planner-locked
    enforcement (per Plan 01-01 ``.ruff.toml`` comment).

    Walks:
      * ``src/gekko/brokers/`` (recursive)
      * ``src/gekko/execution/`` (recursive)
      * ``src/gekko/core/money.py`` (single file)

    For each .py file: strip lines that are pure comments (``#`` after
    ``lstrip``) and then apply the regex ``(?<![A-Za-z_])float\\b``. Any
    match fails the test with the file path so the operator can see exactly
    which line is offending.

    Comment-only mentions of ``float`` (in docstrings or ``# ...``
    explanations) are allowed — the gate is about runtime code, not docs.
    Docstring lines are not pure-comment lines so a triple-quoted
    docstring is technically not stripped — but no plan's docstrings
    mention the bare ``float`` token, so this is fine in practice. If a
    future plan needs to mention ``float`` inside a docstring, name it
    e.g. ``"float-valued"`` or ``"floating-point"`` to avoid tripping the
    gate.
    """
    repo_root = Path(__file__).resolve().parents[2]
    banned = re.compile(r"(?<![A-Za-z_])float\b")

    targets: list[Path] = []
    brokers_dir = repo_root / "src" / "gekko" / "brokers"
    execution_dir = repo_root / "src" / "gekko" / "execution"
    money_file = repo_root / "src" / "gekko" / "core" / "money.py"

    if brokers_dir.is_dir():
        targets.extend(brokers_dir.rglob("*.py"))
    if execution_dir.is_dir():
        targets.extend(execution_dir.rglob("*.py"))
    if money_file.is_file():
        targets.append(money_file)

    offenses: list[tuple[Path, int, str]] = []
    for py in targets:
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Skip pure-comment lines (the file may legitimately discuss
            # ``float`` in a comment explaining why it's banned).
            if line.lstrip().startswith("#"):
                continue
            if banned.search(line):
                offenses.append((py.relative_to(repo_root), lineno, line.rstrip()))

    assert not offenses, (
        "EXEC-01 grep gate failed — `float` is banned in money paths "
        "(src/gekko/brokers/, src/gekko/execution/, src/gekko/core/money.py). "
        "Offenses:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in offenses)
    )
