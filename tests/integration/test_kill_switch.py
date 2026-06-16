"""Wave-0 stub — kill-switch integration tests (5s SLA + parallel cancel).

# WAVE-0 STUB: owned by plan 02-05 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-06 integration slice — the 5s wall-clock SLA from
`/gekko kill CONFIRM` to all open orders cancelled + kill_active=true.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_kill_switch_5s_sla_placeholder() -> None:
    """Will assert kill flows complete in <5s in 9/10 trials (cassette + frozen_time)."""
    pass


def test_kill_switch_cancels_open_orders_in_parallel_placeholder() -> None:
    """Will assert N open orders cancel concurrently (gather, not sequential)."""
    pass
