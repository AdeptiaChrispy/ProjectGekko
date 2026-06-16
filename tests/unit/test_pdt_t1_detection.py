"""Wave-0 stub — PDT (4th round-trip) + T+1 unsettled detection.

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-11 — local OrderGuard pre-checks for:
  - 4th day-trade in rolling 5 business days → reject_code='pdt_rule_local'
  - T+1 unsettled cash → reject_code='t1_settlement'

The 'pdt_rule' (capital-P from broker) and 'pdt_rule_local' (our pre-check) are
distinct so the audit log can distinguish which layer caught the violation.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_pdt_local_4th_round_trip_blocks_placeholder() -> None:
    """Will assert OrderGuardRejected(reject_code='pdt_rule_local')."""
    pass


def test_t1_settlement_blocks_placeholder() -> None:
    """Will assert OrderGuardRejected(reject_code='t1_settlement')."""
    pass
