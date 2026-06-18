"""AST gate: edit-size goes through state machine + executor, never direct place_order.

Plan 03-05 Task 3 (T-03-05-07 / D-27 Knight Capital defense).

Parses slack_handler.py + routes.py and asserts zero direct `place_order` calls
outside src/gekko/execution/executor.py.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _collect_place_order_calls(source_path: Path) -> list[tuple[int, str]]:
    """Return (lineno, call_repr) for every call to place_order in the source."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))

    results: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == "place_order":
                results.append((node.lineno, f"place_order() at line {node.lineno}"))
            elif isinstance(func, ast.Attribute) and func.attr == "place_order":
                results.append((node.lineno, f".place_order() at line {node.lineno}"))
            self.generic_visit(node)

    Visitor().visit(tree)
    return results


def test_edit_size_not_direct_broker() -> None:
    """Asserts no direct place_order call in slack_handler.py or routes.py.

    The invariant: edit-size always flows through PENDING -> APPROVED + executor.
    The executor calls OrderGuard which calls the broker — edit-size never
    bypasses this chain (D-27 Knight Capital defense per T-03-05-07).
    """
    repo_root = Path(__file__).parent.parent.parent

    slack_handler_path = repo_root / "src" / "gekko" / "approval" / "slack_handler.py"
    routes_path = repo_root / "src" / "gekko" / "dashboard" / "routes.py"

    assert slack_handler_path.exists(), f"Missing: {slack_handler_path}"
    assert routes_path.exists(), f"Missing: {routes_path}"

    violations: list[str] = []

    for path in [slack_handler_path, routes_path]:
        calls = _collect_place_order_calls(path)
        for lineno, repr_str in calls:
            violations.append(f"{path.name}:{lineno} — {repr_str}")

    assert not violations, (
        "Direct place_order() calls found outside executor.py "
        "(D-27 Knight Capital defense violated):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
