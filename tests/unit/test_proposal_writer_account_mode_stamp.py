"""Wave-0 stub — BLOCKER #5 runtime half: ProposalWriter stamps account_mode.

# WAVE-0 STUB: owned by plan 02-06 — DO NOT delete the skip until that plan's tasks land

Closes the TOCTOU window between proposal-gen (T0) and approve-click (T1):
ProposalWriter reads strategy.mode + strategy_metadata.live_mode_eligible AT
PROPOSAL-BUILD TIME and stamps the resulting account_mode onto the
TradeProposal. Downstream callers (Slack approve handler, executor) MUST
read account_mode from the proposal row, NOT re-derive from strategy state
at execute-time.

Includes a "promote after stamp" test: a proposal stamped account_mode='PAPER'
remains 'PAPER' even after strategy.mode is flipped to 'live' between T0 and T1.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_proposal_writer_stamps_paper_when_strategy_paper_placeholder() -> None:
    """Will assert account_mode='PAPER' when strategy.mode='paper'."""
    pass


def test_proposal_writer_stamps_live_when_eligible_placeholder() -> None:
    """Will assert account_mode='LIVE' when strategy.mode='live' AND live_mode_eligible."""
    pass


def test_proposal_account_mode_immune_to_post_stamp_strategy_change_placeholder() -> None:
    """TOCTOU: proposal row's account_mode survives strategy.mode flip post-T0."""
    pass
