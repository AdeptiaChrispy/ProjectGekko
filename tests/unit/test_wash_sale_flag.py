"""Wave-0 stub — wash-sale FLAG dict shape.

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land

Locks the wash-sale FLAG dict's keys: {triggered: bool, prior_loss_event_id: str,
window_days_remaining: int, reason: str}. Plan 02-03 writes these into the
TradeProposal.wash_sale_flag field added in plan 02-01 Task 3.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_wash_sale_flag_dict_shape_placeholder() -> None:
    """Will assert flag dict has expected key set."""
    pass
