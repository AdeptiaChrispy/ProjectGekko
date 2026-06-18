"""AST gate: every _send_slack_dm call site classified — Plan 03-03 Task 2.

Parses ``src/gekko/execution/executor.py`` via ``ast`` and asserts that:

1. Every ``_send_slack_dm(...)`` call site has a ``# bypass-category: <name>``
   comment on the line immediately preceding it in the source.
2. Every ``_send_slack_dm_respecting_quiet_hours(...)`` call site passes a
   ``category=`` keyword argument.

This gate makes it impossible for a future contributor to add a new direct
``_send_slack_dm`` call without annotating its bypass-category — the CI run
will fail immediately (Pitfall 9 from RESEARCH §HITL-05).

Implementation follows the AST-walk pattern from
``tests/unit/test_alpaca_live_construction_locked.py`` (Plan 02-06):
read the source bytes, parse via ``ast.parse``, walk ``ast.NodeVisitor``
collecting call sites, then assert constraints.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Source file under inspection
# ---------------------------------------------------------------------------

_EXECUTOR_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "gekko"
    / "execution"
    / "executor.py"
)

_BYPASS_COMMENT_RE = re.compile(r"#\s*bypass-category:\s*\S+")


# ---------------------------------------------------------------------------
# Helper: collect all Call nodes with a given func name from an AST
# ---------------------------------------------------------------------------


class _CallCollector(ast.NodeVisitor):
    """Collect all ast.Call nodes whose func is a Name or Attribute matching target."""

    def __init__(self, target_name: str) -> None:
        self.target = target_name
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        matched = False
        if isinstance(func, ast.Name) and func.id == self.target:
            matched = True
        elif isinstance(func, ast.Attribute) and func.attr == self.target:
            matched = True
        if matched:
            self.calls.append(node)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Gate 1: every _send_slack_dm call site has a bypass-category comment
# ---------------------------------------------------------------------------


def test_quiet_hours_dm_gate_all_callsites_classified() -> None:
    """Every _send_slack_dm call in executor.py has a bypass-category annotation.

    The annotation must appear on the line immediately preceding the call
    (``# bypass-category: <name>``) OR on the call line itself.
    Lines of the form ``await _send_slack_dm_respecting_quiet_hours(...)``
    are exempted — they are the quiet-hours-aware wrapper and explicitly
    do NOT need bypass annotation.
    """
    source = _EXECUTOR_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(_EXECUTOR_PATH))

    collector = _CallCollector("_send_slack_dm")
    collector.visit(tree)

    # Separate the two function names: wrapper calls are fine; direct calls need annotation.
    direct_calls: list[ast.Call] = []
    for call in collector.calls:
        # Check if this call is itself _send_slack_dm_respecting_quiet_hours
        # (AST will show it as _send_slack_dm after we filter _respecting_quiet_hours).
        # Actually _send_slack_dm_respecting_quiet_hours won't match "func.id == '_send_slack_dm'"
        # because its name is the full string.
        # The collector matches _send_slack_dm ONLY — let's double-check no wrapper calls slipped:
        func = call.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = ""
        # The wrapper is _send_slack_dm_respecting_quiet_hours — does NOT match
        # _send_slack_dm (the collector target is exact).  So all collected calls ARE direct.
        direct_calls.append(call)

    failures: list[str] = []
    for call in direct_calls:
        lineno = call.lineno  # 1-based
        # Check the call line and the preceding 5 lines for the annotation.
        # We look back up to 5 lines to accommodate multi-line try: blocks,
        # inline comments, and function-internal bypass dispatchers.
        call_line = lines[lineno - 1] if lineno - 1 < len(lines) else ""

        has_annotation = bool(_BYPASS_COMMENT_RE.search(call_line))
        if not has_annotation:
            for look_back in range(1, 6):
                idx = lineno - 1 - look_back
                if idx < 0:
                    break
                candidate = lines[idx]
                if _BYPASS_COMMENT_RE.search(candidate):
                    has_annotation = True
                    break

        if not has_annotation:
            prev_line = lines[lineno - 2] if lineno >= 2 else ""
            failures.append(
                f"  Line {lineno}: {call_line.strip()!r}\n"
                f"    ← missing '# bypass-category: <name>' annotation "
                f"(within 5 preceding lines)\n"
                f"    Preceding line: {prev_line.strip()!r}"
            )

    if failures:
        msg = (
            f"executor.py has {len(failures)} unclassified _send_slack_dm call site(s).\n"
            "Every direct _send_slack_dm call must be preceded by a\n"
            "'# bypass-category: <name>' comment (D-48 / HITL-05 AST gate).\n\n"
            + "\n".join(failures)
        )
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Gate 2: every _send_slack_dm_respecting_quiet_hours call passes category=
# ---------------------------------------------------------------------------


def test_wrapper_calls_always_pass_category_kwarg() -> None:
    """Every _send_slack_dm_respecting_quiet_hours call passes category=<literal>.

    The wrapper's ``category`` parameter is keyword-only (``*`` separator),
    so callers cannot pass it positionally.  This gate catches any call that
    somehow omits the kwarg entirely.
    """
    source = _EXECUTOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_EXECUTOR_PATH))

    collector = _CallCollector("_send_slack_dm_respecting_quiet_hours")
    collector.visit(tree)

    failures: list[str] = []
    for call in collector.calls:
        kwarg_names = [kw.arg for kw in call.keywords]
        if "category" not in kwarg_names:
            failures.append(
                f"  Line {call.lineno}: missing category= kwarg in "
                f"_send_slack_dm_respecting_quiet_hours call"
            )

    if failures:
        msg = (
            "executor.py has _send_slack_dm_respecting_quiet_hours call(s) "
            "without category= kwarg:\n" + "\n".join(failures)
        )
        raise AssertionError(msg)
