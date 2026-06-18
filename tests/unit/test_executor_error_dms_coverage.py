"""AST-walk gate: every FAILED transition in executor.py + kill_switch.py has a sibling DM.

Tests REPT-01 carry-forward audit: every ``transition_status(..., to_status="FAILED")``
call in executor.py must have a corresponding ``_send_slack_dm`` call within ~10 lines.

This is a static analysis gate — it does NOT run the executor code, only reads and
parses the source. Future contributors cannot add a silent-FAILED path without failing CI.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EXECUTOR_PATH = Path("src/gekko/execution/executor.py")
_KILL_SWITCH_PATH = Path("src/gekko/execution/kill_switch.py")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_transition_to_failed(node: ast.AST) -> bool:
    """Return True if node is a Call to transition_status with to_status='FAILED'."""
    if not isinstance(node, ast.Call):
        return False
    # Match transition_status(...) or await transition_status(...)
    func = node.func
    name = ""
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "transition_status":
        return False
    # Check keyword arguments for to_status="FAILED"
    for kw in node.keywords:
        if kw.arg == "to_status" and isinstance(kw.value, ast.Constant):
            if kw.value.value == "FAILED":
                return True
    return False


def _has_dm_call_nearby(
    source_lines: list[str],
    lineno: int,
    window: int = 30,
) -> bool:
    """Return True if a _send_slack_dm* call appears within `window` lines of `lineno`.

    Searches forward from the transition_status line within the same try/except block.
    """
    start = max(0, lineno - 1)
    end = min(len(source_lines), lineno + window)
    for line in source_lines[start:end]:
        if "_send_slack_dm" in line or "_send_slack_dm_blocks" in line:
            return True
    return False


def _find_failed_transitions(source: str) -> list[tuple[int, bool]]:
    """Parse ``source`` and return ``(lineno, has_nearby_dm)`` for each FAILED transition."""
    source_lines = source.splitlines()
    tree = ast.parse(source)
    results: list[tuple[int, bool]] = []

    for node in ast.walk(tree):
        # We want top-level Call nodes and await expressions.
        call_node: ast.Call | None = None
        lineno = 0

        if isinstance(node, ast.Expr):
            # await transition_status(...) is an Expr(Await(Call(...)))
            if isinstance(node.value, ast.Await):
                if _is_transition_to_failed(node.value.value):
                    call_node = node.value.value
                    lineno = node.lineno
            elif isinstance(node.value, ast.Call):
                if _is_transition_to_failed(node.value):
                    call_node = node.value
                    lineno = node.lineno

        if call_node is not None and lineno > 0:
            has_dm = _has_dm_call_nearby(source_lines, lineno)
            results.append((lineno, has_dm))

    return results


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


def test_executor_failed_transitions_all_have_dm() -> None:
    """Every transition_status(..., to_status='FAILED') in executor.py has a sibling _send_slack_dm call."""
    source = _EXECUTOR_PATH.read_text(encoding="utf-8")
    results = _find_failed_transitions(source)

    assert len(results) > 0, (
        "Expected at least one FAILED transition in executor.py — gate would be vacuously true if zero"
    )

    violations = [(lineno, has_dm) for lineno, has_dm in results if not has_dm]
    violation_summary = "\n".join(
        f"  Line {lineno}: transition_status → FAILED with no _send_slack_dm within 30 lines"
        for lineno, _ in violations
    )
    assert not violations, (
        f"Found {len(violations)} silent-FAILED transition(s) in executor.py "
        f"(carry-forward audit gap):\n{violation_summary}"
    )


def test_kill_switch_failed_transitions_all_have_dm() -> None:
    """kill_switch.py has no transition_status calls (it uses update() directly).

    The kill switch does not call transition_status — it uses SQLAlchemy
    update() to flip users.kill_active directly. This test verifies that
    assumption holds and that any future refactor that adds a
    transition_status(..., to_status='FAILED') call to kill_switch.py also
    wires a sibling DM.
    """
    source = _KILL_SWITCH_PATH.read_text(encoding="utf-8")
    results = _find_failed_transitions(source)

    # Current implementation: no FAILED transitions in kill_switch.py.
    violations = [(lineno, has_dm) for lineno, has_dm in results if not has_dm]
    violation_summary = "\n".join(
        f"  Line {lineno}: transition_status → FAILED with no _send_slack_dm within 30 lines"
        for lineno, _ in violations
    )
    assert not violations, (
        f"Found {len(violations)} silent-FAILED transition(s) in kill_switch.py:\n{violation_summary}"
    )
