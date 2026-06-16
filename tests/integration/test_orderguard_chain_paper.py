"""Wave-0 stub — 4-event cap_rejection audit chain on PAPER.

# WAVE-0 STUB: owned by plan 02-02 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-04 audit chain — when OrderGuard rejects a PAPER proposal for a
hard-cap violation, the audit chain accumulates [decision, proposal,
cap_rejection, error] in that order, with walk_chain returning []
(chain intact).
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_paper_cap_rejection_audit_chain_4_events_placeholder() -> None:
    """Will assert the 4-event chain shape + walk_chain([])."""
    pass
