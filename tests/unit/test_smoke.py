"""Plan 01-01 Task 4 — package import smoke test.

Per VALIDATION.md §Wave 0 Requirements last bullet: every `src/gekko/`
sub-package must import without error. This is the canary that catches
broken `__init__.py` files early before any further plan can build on top.
"""

from __future__ import annotations

import importlib

import pytest

GEKKO_MODULES = (
    "gekko",
    "gekko.cli",
    "gekko.core",
    "gekko.schemas",
    "gekko.db",
    "gekko.brokers",
    "gekko.audit",
    "gekko.agent",
    "gekko.agent.tools",
    "gekko.execution",
    "gekko.approval",
    "gekko.reporter",
    "gekko.scheduler",
    "gekko.slack",
    "gekko.dashboard",
    "gekko.vault",
)


@pytest.mark.parametrize("module_name", GEKKO_MODULES)
def test_imports(module_name: str) -> None:
    """Import every gekko.* module — no exceptions allowed."""
    module = importlib.import_module(module_name)
    assert module is not None, f"{module_name!r} imported as None"


def test_package_version_string() -> None:
    """The top-level package exposes a __version__ string per Task 3 scaffold."""
    import gekko

    assert isinstance(gekko.__version__, str), "gekko.__version__ must be a str"
    assert gekko.__version__, "gekko.__version__ must be non-empty"
