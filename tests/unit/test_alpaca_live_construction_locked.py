"""BLOCKER #4 grep gate — Plan 02-06 Task 1.

Locks ``AlpacaBroker(...)`` calls that pass ``paper=False`` or
``_allow_live=True`` to a single site:
``src/gekko/execution/executor.py::_build_broker``. Any other site under
``src/gekko/`` that constructs ``AlpacaBroker(paper=False, ...)`` or
``AlpacaBroker(..., _allow_live=True)`` fails this test — prevents an
accidental future refactor from constructing a live AlpacaBroker outside
the credential-gated ``_build_broker`` path.

The AST walks ALL ``.py`` files under ``src/gekko/`` and inspects every
:class:`ast.Call` node whose callee is named ``AlpacaBroker``. For each
such call, the test inspects the keyword args:

  * ``paper=False`` (literal False keyword) → forbidden outside ``_build_broker``.
  * ``_allow_live=True`` (literal True keyword) → forbidden outside ``_build_broker``.

NB: The ``BrokerCredential(... paper=False ...)`` ORM call in
``src/gekko/vault/credentials.py`` is NOT an ``AlpacaBroker`` call, so
this AST-aware gate correctly ignores it. A naive text-grep would
false-positive there.

We also confirm the constructor's argument-check behavior holds: a
direct ``AlpacaBroker(paper=False)`` from user code still raises
``BrokerConfigError`` (defense in depth).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src" / "gekko"

#: Permitted sites for ``AlpacaBroker(paper=False, _allow_live=True)``. Both
#: live in ``executor.py`` and resolve live credentials from the vault before
#: constructing the broker:
#:   * ``_build_broker`` — the HITL / execute path (the original single site).
#:   * ``_build_broker_for_anomaly`` — Phase-5 post-fill anomaly reflex. It
#:     builds a RAW (un-OrderGuard-wrapped) broker that only READS positions
#:     and CANCELS on breach; it gates on ``load_live_credentials`` exactly
#:     like ``_build_broker``, so it is a deliberate, credential-gated site —
#:     not an accidental live-broker construction the grep gate guards against.
_ALLOWED_FILE: Path = _SRC_ROOT / "execution" / "executor.py"
_ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {"_build_broker", "_build_broker_for_anomaly"}
)
#: Back-compat alias: the positive-control test asserts the original site still
#: carries the live path.
_ALLOWED_FUNCTION: str = "_build_broker"


def _all_python_files() -> list[Path]:
    """Every .py file under src/gekko/."""
    return [p for p in _SRC_ROOT.rglob("*.py") if p.is_file()]


def _enclosing_function(
    tree: ast.Module, target_lineno: int
) -> str | None:
    """Return the name of the innermost FunctionDef enclosing ``target_lineno``."""
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = node.end_lineno or node.lineno
            if start <= target_lineno <= end:
                # innermost = highest start lineno
                if best is None or start > best[0]:
                    best = (start, node.name)
    return best[1] if best else None


def _is_alpaca_broker_call(call: ast.Call) -> bool:
    """True iff this Call node's callee is the name ``AlpacaBroker``."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "AlpacaBroker":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "AlpacaBroker":
        return True
    return False


def _keyword_has_value(call: ast.Call, name: str, value: object) -> bool:
    """True iff the call has ``kwname=<literal value>`` exactly."""
    for kw in call.keywords:
        if kw.arg != name:
            continue
        if isinstance(kw.value, ast.Constant) and kw.value.value is value:
            return True
    return False


def test_alpaca_broker_paper_false_locked_to_build_broker() -> None:
    """Every `AlpacaBroker(paper=False, ...)` call must live inside `_build_broker`."""
    files = _all_python_files()
    assert files, "no .py files found under src/gekko/"

    violations: list[tuple[Path, int, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:  # pragma: no cover
            pytest.fail(f"failed to parse {path}: {exc}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_alpaca_broker_call(node):
                continue
            if not _keyword_has_value(node, "paper", False):
                continue
            enclosing = _enclosing_function(tree, node.lineno)
            if path == _ALLOWED_FILE and enclosing in _ALLOWED_FUNCTIONS:
                continue
            violations.append(
                (path, node.lineno, enclosing or "<module>")
            )

    if violations:
        msg_lines = [
            "AlpacaBroker(paper=False, ...) outside the allowed builders "
            f"{sorted(_ALLOWED_FUNCTIONS)} (BLOCKER #4 grep gate):"
        ]
        for path, line_no, enclosing in violations:
            rel = path.relative_to(_SRC_ROOT.parent.parent)
            msg_lines.append(f"  {rel}:{line_no} inside {enclosing!r}")
        pytest.fail("\n".join(msg_lines))


def test_alpaca_broker_allow_live_true_locked_to_build_broker() -> None:
    """Every `AlpacaBroker(_allow_live=True, ...)` call must live inside `_build_broker`."""
    files = _all_python_files()
    assert files, "no .py files found under src/gekko/"

    violations: list[tuple[Path, int, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:  # pragma: no cover
            pytest.fail(f"failed to parse {path}: {exc}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_alpaca_broker_call(node):
                continue
            if not _keyword_has_value(node, "_allow_live", True):
                continue
            enclosing = _enclosing_function(tree, node.lineno)
            if path == _ALLOWED_FILE and enclosing in _ALLOWED_FUNCTIONS:
                continue
            violations.append(
                (path, node.lineno, enclosing or "<module>")
            )

    if violations:
        msg_lines = [
            "AlpacaBroker(_allow_live=True, ...) outside the allowed builders "
            f"{sorted(_ALLOWED_FUNCTIONS)} (BLOCKER #4 grep gate):"
        ]
        for path, line_no, enclosing in violations:
            rel = path.relative_to(_SRC_ROOT.parent.parent)
            msg_lines.append(f"  {rel}:{line_no} inside {enclosing!r}")
        pytest.fail("\n".join(msg_lines))


def test_build_broker_actually_uses_paper_false_and_allow_live_true() -> None:
    """Positive control — _build_broker DOES carry the live path.

    Confirms the AST gate isn't silently passing on a stub where the live
    branch was removed. If a future refactor removed the
    ``AlpacaBroker(paper=False, _allow_live=True)`` call, this positive
    control fails — surfacing the regression loudly instead of letting the
    forbidden-literal-tests pass vacuously.
    """
    text = _ALLOWED_FILE.read_text(encoding="utf-8")
    tree = ast.parse(text)

    found_paper_false = False
    found_allow_live_true = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_alpaca_broker_call(node):
            continue
        enclosing = _enclosing_function(tree, node.lineno)
        if enclosing != _ALLOWED_FUNCTION:
            continue
        if _keyword_has_value(node, "paper", False):
            found_paper_false = True
        if _keyword_has_value(node, "_allow_live", True):
            found_allow_live_true = True

    assert found_paper_false, (
        "_build_broker is missing the AlpacaBroker(paper=False, ...) call — "
        "the live branch must exist for plan 02-06 to be coherent"
    )
    assert found_allow_live_true, (
        "_build_broker is missing the AlpacaBroker(..., _allow_live=True) "
        "call — BLOCKER #4 grep gate requires _build_broker actively uses "
        "the live opt-in"
    )


def test_alpaca_broker_constructor_still_blocks_naive_paper_false() -> None:
    """Direct AlpacaBroker(paper=False) without _allow_live still raises.

    Layer-1 guard in the constructor still fires when called from
    non-vetted code (i.e., everything that ISN'T _build_broker).
    """
    from gekko.brokers.alpaca import AlpacaBroker
    from gekko.core.errors import BrokerConfigError

    with pytest.raises(BrokerConfigError):
        AlpacaBroker(api_key="a", secret_key="b", paper=False)


def test_alpaca_broker_paper_path_unchanged() -> None:
    """Paper construction still works without any new kwargs."""
    from gekko.brokers.alpaca import AlpacaBroker

    broker = AlpacaBroker(api_key="a", secret_key="b", paper=True)
    assert broker.is_paper is True
