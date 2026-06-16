"""Wave-0 stub — BLOCKER #4 grep gate locking AlpacaBroker live construction.

# WAVE-0 STUB: owned by plan 02-06 — DO NOT delete the skip until that plan's tasks land

Module-level grep gate locking `_allow_live=True`, `_allow_live = True`, and
`paper=False` literals to `src/gekko/execution/executor.py::_build_broker`
ONLY. Any other occurrence of these tokens inside `src/gekko/` fails the test
— prevents an accidental future refactor from constructing a live broker
outside the credential-gated _build_broker path.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_no_paper_false_outside_build_broker_placeholder() -> None:
    """Will grep src/gekko/ for paper=False; only _build_broker may use it."""
    pass


def test_no_allow_live_true_outside_build_broker_placeholder() -> None:
    """Will grep src/gekko/ for _allow_live=True; only _build_broker may use it."""
    pass
