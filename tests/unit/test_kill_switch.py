"""Wave-0 stub — kill-switch unit tests (DB column persistence + transitions).

# WAVE-0 STUB: owned by plan 02-05 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-06 unit-level slice — users.kill_active column persists across
sessions, OrderGuardRejected(reject_code='kill_active') raised on any new
place_order call while kill is active.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_kill_active_blocks_place_order_placeholder() -> None:
    """Will assert that users.kill_active=True blocks place_order."""
    pass


def test_kill_state_persists_across_session_placeholder() -> None:
    """Will assert kill_active persists in DB after engine.dispose+reopen."""
    pass
