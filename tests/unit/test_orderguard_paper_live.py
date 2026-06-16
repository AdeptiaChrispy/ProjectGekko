"""Wave-0 stub — OrderGuard paper/live pairing tests.

# WAVE-0 STUB: owned by plan 02-02 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-05 — the 4-6 paper/live mismatch cases:
  - paper_live_mismatch_broker
  - paper_live_mismatch_account
  - paper_live_mismatch_credential (per BLOCKER #7 — emitted from
    check_paper_live_pairing, NOT cap_rejection handler)
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_paper_live_mismatch_broker_placeholder() -> None:
    """Will assert OrderGuardRejected(reject_code='paper_live_mismatch_broker')."""
    pass


def test_paper_live_mismatch_credential_placeholder() -> None:
    """Will assert OrderGuardRejected(reject_code='paper_live_mismatch_credential')."""
    pass
