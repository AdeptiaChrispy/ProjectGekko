"""AST gate — SessionMiddleware registered BEFORE banner-state middleware.

Plan 03-05 Task 1 (Starlette reverse-order execution rule).

The invariant: in `create_app()`, `app.add_middleware(SessionMiddleware, ...)` must
appear BEFORE any `@app.middleware("http")` decorator in source order. Starlette
wraps middleware in the inverse of registration order — `add_middleware` inserts into
the outermost position when executed last, but since we need `request.session` to be
available inside the banner-state middleware (the inner middleware), the
SessionMiddleware MUST be registered first so Starlette wraps it outermost.

The concrete rule: in the source text of `create_app()`, the line containing
`add_middleware(SessionMiddleware` must come before any line that is an
`@app.middleware("http")` decorator call.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_session_middleware_before_banner_middleware() -> None:
    """Parse create_app() and assert add_middleware(SessionMiddleware) precedes
    @app.middleware('http') in source order."""
    src_path = (
        Path(__file__).parent.parent.parent
        / "src" / "gekko" / "dashboard" / "app.py"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    add_middleware_lineno: int | None = None
    http_middleware_lineno: int | None = None

    for node in ast.walk(tree):
        # Find: app.add_middleware(SessionMiddleware, ...)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "add_middleware"
            ):
                # Check first arg is SessionMiddleware
                if call.args and isinstance(call.args[0], ast.Name):
                    if call.args[0].id == "SessionMiddleware":
                        add_middleware_lineno = node.lineno

        # Find: @app.middleware("http") — decorator call on a function def
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                # app.middleware("http") is a Call with func=Attribute(attr="middleware")
                if isinstance(deco, ast.Call):
                    if (
                        isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "middleware"
                    ):
                        if deco.args and isinstance(deco.args[0], ast.Constant):
                            if deco.args[0].value == "http":
                                if http_middleware_lineno is None:
                                    http_middleware_lineno = deco.lineno

    assert add_middleware_lineno is not None, (
        "add_middleware(SessionMiddleware, ...) not found in src/gekko/dashboard/app.py. "
        "Task 1 requires adding SessionMiddleware via add_middleware() in create_app()."
    )
    assert http_middleware_lineno is not None, (
        "@app.middleware('http') not found in src/gekko/dashboard/app.py. "
        "Expected the banner-state middleware decorator."
    )
    assert add_middleware_lineno < http_middleware_lineno, (
        f"SessionMiddleware add_middleware (line {add_middleware_lineno}) must appear "
        f"BEFORE @app.middleware('http') decorator (line {http_middleware_lineno}) "
        f"per Starlette reverse-order execution rule. "
        f"Move add_middleware(SessionMiddleware, ...) earlier in create_app()."
    )
