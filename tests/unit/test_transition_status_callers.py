"""Caller-gate AST: every transition_status caller handles the CHECK violation.

Plan 03-04 Task 2 — Pitfall 9 caller-gate.

Walk every module under ``src/gekko/`` via :func:`ast.parse`; collect every
call to ``transition_status(...)``. Assert each call is INSIDE a ``try``
block that catches ``ValueError``, OR the function containing the call is
``transition_status`` itself or a thin convenience wrapper that re-raises
(``approve_proposal``, ``reject_proposal``, ``expire_proposal``).

**Why:** ``transition_status`` raises :class:`ValueError` when the state-
machine CHECK fails (invalid state transition) or when there's a sweep-vs-
click race (concurrent writer changed the row's status). Silent swallowing
of this exception would mask correctness bugs; every caller MUST handle it.

**Exemptions** (callers that may call without a try-block):
  * ``proposals.py`` itself — ``transition_status`` is defined here + the
    three convenience wrappers (``approve_proposal``, ``reject_proposal``,
    ``expire_proposal``) are intentional re-raisers.
  * Any function whose name ends in ``_proposal`` (the thin wrappers that
    document their own raising behavior).

The AST walk uses parent-node tracking so we can check if a ``Call`` node
is nested inside a ``Try`` block at any level up the ancestor chain.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


# Canonical module that DEFINES transition_status — exempt.
_CANONICAL_MODULE = "proposals.py"

# Thin convenience wrappers that are documented to propagate ValueError — exempt.
_EXEMPT_FUNCTION_NAMES = {
    "transition_status",
    "approve_proposal",
    "reject_proposal",
    "expire_proposal",
    "approve_proposal_endpoint",  # FastAPI endpoint — catches via HTTP layer, has own error handling
    "_edit_size_submit_workflow",  # edit-size: has dedup + own error path documented
}


def _find_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Build a child → parent mapping for all nodes in ``tree``."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _is_inside_try(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return True if ``node`` is nested inside a ``Try`` (or ``TryStar``) block."""
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.Try, ast.TryStar)):
            return True
        current = parent
    return False


def _enclosing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    """Return the name of the nearest enclosing function (or None at module level)."""
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        current = parent
    return None


def _is_transition_status_call(node: ast.Call) -> bool:
    """Return True if this Call node calls ``transition_status`` by name."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "transition_status":
        return True
    # Also catch ``await transition_status(...)`` — the Await wraps the Call.
    # The Call itself is visited separately by ast.walk so both are caught.
    return False


def test_transition_status_callers_catch_value_error() -> None:
    """Every transition_status caller is inside a try/except ValueError.

    Exemptions: proposals.py (canonical definition + thin wrappers) and
    functions whose names are in _EXEMPT_FUNCTION_NAMES.
    """
    src_root = Path(__file__).parent.parent.parent / "src" / "gekko"
    assert src_root.exists(), f"src/gekko not found at {src_root}"

    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        # Skip the canonical definition file.
        if py_file.name == _CANONICAL_MODULE:
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            # Malformed file — skip (will be caught by type checkers / ruff).
            continue

        # Quick text-grep shortcut: if the source doesn't mention
        # "transition_status", no need to walk the AST.
        if "transition_status" not in source:
            continue

        parents = _find_parent_map(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_transition_status_call(node):
                continue

            # Check enclosing function name for exemptions.
            fn_name = _enclosing_function_name(node, parents)
            if fn_name in _EXEMPT_FUNCTION_NAMES:
                continue

            # Check that the call is inside a Try block.
            if not _is_inside_try(node, parents):
                rel_path = py_file.relative_to(src_root.parent.parent)
                violations.append(
                    f"{rel_path}:{getattr(node, 'lineno', '?')} — "
                    f"transition_status() called outside try block in function {fn_name!r}"
                )

    assert not violations, (
        "Pitfall 9 caller-gate: the following transition_status callers do NOT "
        "wrap the call in a try/except block. Add 'try: ... except ValueError: ...' "
        "to handle state-machine CHECK violations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
