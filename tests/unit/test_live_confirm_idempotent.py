"""Wave-0 stub — HITL-06 idempotent double-click on live confirm.

# WAVE-0 STUB: owned by plan 02-06 — DO NOT delete the skip until that plan's tasks land

Covers HITL-06 — the operator double-clicking the dashboard "Confirm Live"
button MUST NOT advance the state machine twice (no double-execute). The
APPROVED_LIVE → EXECUTING transition is idempotent on same-target-status
per the Phase-1 state-machine invariant.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_double_live_confirm_is_idempotent_placeholder() -> None:
    """Will assert two confirm POSTs produce exactly one EXECUTING event."""
    pass
