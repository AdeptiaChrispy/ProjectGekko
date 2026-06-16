"""Wave-0 stub — kill-switch cross-restart persistence test.

# WAVE-0 STUB: owned by plan 02-05 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-06 D-36 — after process restart with kill_active=true already in
DB, the runtime refuses all new place_order calls before they reach OrderGuard,
DMs operator, and does NOT auto-clear the flag.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_kill_state_survives_process_restart_placeholder() -> None:
    """Will assert kill_active=true survives gekko-serve teardown + cold boot."""
    pass
