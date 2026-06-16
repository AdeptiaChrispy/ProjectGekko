"""Alpaca retry-decorator gate — plan 02-03 Task 1 (EXEC-03 + EXEC-08).

Covers two invariants:

* **EXEC-03 / Pitfall 4 / Knight Capital (LOAD-BEARING):** Order-POST
  methods on every broker AND on the OrderGuard wrapper carry ZERO retry
  decorators. Enforced via:

    1. AST-walk gate (canonical per WARNING #2 fix) — parses the source
       file, finds ``AlpacaBroker.place_order``, ``AlpacaBroker.cancel_order``,
       and ``OrderGuard.place_order``, and asserts
       ``len(node.decorator_list) == 0``. Catches comment-out tricks /
       whitespace games / ``# noqa`` markers that text-grep missed.
    2. Runtime introspection — asserts the bound functions have no
       ``__wrapped__`` attribute (tenacity sets this on decorated funcs).

* **EXEC-08:** Broker GET methods (``get_account``, ``get_positions``,
  ``get_quote``, ``get_order_by_client_order_id``) ARE decorated with
  ``@retry_on_rate_limit``. Asserted via both AST positive control
  (decorator's source name contains ``retry_on_rate_limit``) AND runtime
  ``__wrapped__`` introspection.

The functional retry-on-429 behaviors live in
``tests/unit/test_rate_limit_backoff.py`` (separate file for the
behavioral assertions; this file is the source-bytes + structure gate).
"""

from __future__ import annotations

import ast
from pathlib import Path


# ---------------------------------------------------------------------------
# AST-walk helpers (WARNING #2 fix — canonical EXEC-03 enforcement)
# ---------------------------------------------------------------------------


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef:
    """Return the top-level ClassDef named ``class_name`` from ``tree``."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    msg = f"class {class_name!r} not found in module"
    raise AssertionError(msg)


def _find_method(
    cls: ast.ClassDef, method_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the (Async)FunctionDef named ``method_name`` inside ``cls``."""
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        ):
            return node
    msg = f"method {method_name!r} not found in class {cls.name!r}"
    raise AssertionError(msg)


def _decorator_source_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Return the source-name representations of every decorator on ``node``.

    Handles both ``@foo`` (Name) and ``@foo.bar`` (Attribute) and
    ``@foo(...)`` (Call). The returned strings let tests assert presence
    of ``retry_on_rate_limit`` without parsing the AST themselves.
    """
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
        else:  # pragma: no cover - defensive
            names.append(ast.dump(dec))
    return names


# ---------------------------------------------------------------------------
# AST gate — AlpacaBroker.place_order MUST have zero decorators
# ---------------------------------------------------------------------------


def test_alpaca_place_order_has_zero_decorators() -> None:
    """Source-bytes AST assertion that AlpacaBroker.place_order is undecorated.

    Pitfall 4 / EXEC-03 / BLOCKER #4 — Knight-Capital insurance. Order POSTs
    must NEVER auto-retry. The AST walk inspects the parsed tree directly
    (per WARNING #2 fix) so comment-out tricks / whitespace games cannot
    bypass it.
    """
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "place_order")
    assert method.decorator_list == [], (
        "AlpacaBroker.place_order has unexpected decorators "
        "(Pitfall 4 / EXEC-03 / Knight-Capital invariant): "
        f"{_decorator_source_names(method)!r}"
    )


def test_alpaca_cancel_order_has_zero_decorators() -> None:
    """AlpacaBroker.cancel_order MUST also remain undecorated.

    Per RESEARCH §6 Open Question #1: a 429 retry storm during a kill is
    the worst possible timing. Plan 02-05's kill switch relies on cancel
    failing fast within the 4s timeout window — a tenacity decorator
    would convert that to ~5 minutes of retries.
    """
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "cancel_order")
    assert method.decorator_list == [], (
        "AlpacaBroker.cancel_order has unexpected decorators "
        "(RESEARCH §6 Open Question #1 — explicit no-retry decision): "
        f"{_decorator_source_names(method)!r}"
    )


def test_orderguard_place_order_has_zero_decorators() -> None:
    """OrderGuard.place_order MUST also remain undecorated.

    Defense in depth: even if a future refactor accidentally decorates
    the outer wrapper, this AST gate catches it before the source ever
    runs.
    """
    import gekko.execution.orderguard as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "OrderGuard")
    method = _find_method(cls, "place_order")
    assert method.decorator_list == [], (
        "OrderGuard.place_order has unexpected decorators "
        "(Pitfall 4 / EXEC-03 — Knight-Capital invariant): "
        f"{_decorator_source_names(method)!r}"
    )


# ---------------------------------------------------------------------------
# AST positive controls — confirm GETs ARE decorated
# ---------------------------------------------------------------------------


def test_alpaca_get_account_has_retry_decorator_ast() -> None:
    """Positive control: ``AlpacaBroker.get_account`` carries 1 decorator,
    and its name contains ``retry_on_rate_limit``. Confirms the AST
    inspection is reading the real tree (not a stub) — if this fails but
    ``test_alpaca_place_order_has_zero_decorators`` passes, the entire
    AST-walk gate would be silently meaningless.
    """
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "get_account")
    names = _decorator_source_names(method)
    assert len(method.decorator_list) >= 1, (
        f"AlpacaBroker.get_account must carry @retry_on_rate_limit "
        f"(EXEC-08); found decorators: {names!r}"
    )
    assert "retry_on_rate_limit" in names, (
        f"AlpacaBroker.get_account decorator name must include "
        f"'retry_on_rate_limit'; found: {names!r}"
    )


def test_alpaca_get_positions_has_retry_decorator_ast() -> None:
    """AST positive control: ``get_positions`` is decorated."""
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "get_positions")
    names = _decorator_source_names(method)
    assert "retry_on_rate_limit" in names, (
        f"AlpacaBroker.get_positions must be decorated with "
        f"@retry_on_rate_limit; found: {names!r}"
    )


def test_alpaca_get_quote_has_retry_decorator_ast() -> None:
    """AST positive control: ``get_quote`` is decorated."""
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "get_quote")
    names = _decorator_source_names(method)
    assert "retry_on_rate_limit" in names, (
        f"AlpacaBroker.get_quote must be decorated with "
        f"@retry_on_rate_limit; found: {names!r}"
    )


def test_alpaca_get_order_by_client_order_id_has_retry_decorator_ast() -> None:
    """AST positive control: the Pitfall-4 lookup probe is decorated."""
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = _find_class(tree, "AlpacaBroker")
    method = _find_method(cls, "get_order_by_client_order_id")
    names = _decorator_source_names(method)
    assert "retry_on_rate_limit" in names, (
        f"AlpacaBroker.get_order_by_client_order_id must be decorated "
        f"with @retry_on_rate_limit; found: {names!r}"
    )


# ---------------------------------------------------------------------------
# Runtime introspection — __wrapped__ attribute confirms tenacity decoration
# ---------------------------------------------------------------------------


def test_alpaca_get_account_runtime_wrapped() -> None:
    """Runtime check: tenacity sets ``__wrapped__`` on decorated functions."""
    from gekko.brokers.alpaca import AlpacaBroker

    assert hasattr(AlpacaBroker.get_account, "__wrapped__"), (
        "AlpacaBroker.get_account must have __wrapped__ attribute "
        "(tenacity decoration confirmed at runtime)"
    )


def test_alpaca_get_positions_runtime_wrapped() -> None:
    from gekko.brokers.alpaca import AlpacaBroker

    assert hasattr(AlpacaBroker.get_positions, "__wrapped__"), (
        "AlpacaBroker.get_positions must have __wrapped__ attribute"
    )


def test_alpaca_get_quote_runtime_wrapped() -> None:
    from gekko.brokers.alpaca import AlpacaBroker

    assert hasattr(AlpacaBroker.get_quote, "__wrapped__"), (
        "AlpacaBroker.get_quote must have __wrapped__ attribute"
    )


def test_alpaca_get_order_by_client_order_id_runtime_wrapped() -> None:
    from gekko.brokers.alpaca import AlpacaBroker

    assert hasattr(AlpacaBroker.get_order_by_client_order_id, "__wrapped__"), (
        "AlpacaBroker.get_order_by_client_order_id must have __wrapped__ "
        "attribute (tenacity decoration confirmed at runtime)"
    )


# ---------------------------------------------------------------------------
# Negative runtime introspection — POSTs MUST NOT have __wrapped__
# ---------------------------------------------------------------------------


def test_alpaca_place_order_not_wrapped_runtime() -> None:
    """Runtime check: place_order must NOT carry __wrapped__ (no tenacity)."""
    from gekko.brokers.alpaca import AlpacaBroker

    assert not hasattr(AlpacaBroker.place_order, "__wrapped__"), (
        "AlpacaBroker.place_order must NOT have __wrapped__ "
        "(EXEC-03 / Knight Capital invariant — order POSTs never retry)"
    )


def test_alpaca_cancel_order_not_wrapped_runtime() -> None:
    """Runtime check: cancel_order also must NOT carry __wrapped__."""
    from gekko.brokers.alpaca import AlpacaBroker

    assert not hasattr(AlpacaBroker.cancel_order, "__wrapped__"), (
        "AlpacaBroker.cancel_order must NOT have __wrapped__ "
        "(RESEARCH §6 Open Question #1 — kill switch path stays fast-fail)"
    )


def test_orderguard_place_order_not_wrapped_runtime() -> None:
    """Runtime check: OrderGuard.place_order also must NOT carry __wrapped__."""
    from gekko.execution.orderguard import OrderGuard

    assert not hasattr(OrderGuard.place_order, "__wrapped__"), (
        "OrderGuard.place_order must NOT have __wrapped__ "
        "(EXEC-03 — outer wrapper also stays zero-decorator)"
    )


# ---------------------------------------------------------------------------
# Grep-gate defense-in-depth: tenacity / retry_on_rate_limit must NOT appear
# anywhere in src/gekko/execution/orderguard.py
# ---------------------------------------------------------------------------


def test_orderguard_module_does_not_import_tenacity() -> None:
    """The OrderGuard module is the firewall layer — never imports tenacity.

    Defense in depth: even if a future refactor accidentally added an
    ``@retry_on_rate_limit`` import to the orderguard module, this test
    fails before the decorator can be applied. Mirrors Plan 01-08's
    ``claude_agent_sdk`` grep gate over the executor.

    Walks the AST instead of doing a string grep — the module docstring
    legitimately documents the no-retry policy and references the word
    "tenacity" descriptively; only actual ``import`` statements + uses
    of the ``retry_on_rate_limit`` identifier should fail this gate.
    """
    import gekko.execution.orderguard as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    forbidden_modules = {"tenacity", "gekko.brokers._retry"}
    forbidden_names = {"retry_on_rate_limit"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules, (
                    f"src/gekko/execution/orderguard.py imports forbidden "
                    f"module {alias.name!r} (EXEC-03 defense in depth)"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                assert node.module not in forbidden_modules, (
                    f"src/gekko/execution/orderguard.py imports from "
                    f"forbidden module {node.module!r} (EXEC-03)"
                )
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"src/gekko/execution/orderguard.py imports "
                        f"forbidden name {alias.name!r} (EXEC-03)"
                    )
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names, (
                f"src/gekko/execution/orderguard.py references forbidden "
                f"name {node.id!r} at line {node.lineno} (EXEC-03)"
            )
