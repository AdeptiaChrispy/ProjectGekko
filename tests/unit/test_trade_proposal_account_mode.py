"""Wave-0 stub — BLOCKER #5 TradeProposal.account_mode required Literal field.

# WAVE-0 STUB: owned by plan 02-01 Task 3 — DO NOT delete the skip until that plan's tasks land

Plan 02-01 Task 3 replaces this stub with the 7 account_mode behaviors:
required field, "PAPER"/"LIVE" pass, lowercase fails, "MARGIN" fails,
account_mode IS in _runtime_only, account_mode NOT in
_PROPOSE_TRADE_SCHEMA properties.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_account_mode_required_field_placeholder() -> None:
    """Will assert missing account_mode raises pydantic.ValidationError."""
    pass


def test_account_mode_in_runtime_only_placeholder() -> None:
    """Will assert account_mode is stripped from LLM-visible schema."""
    pass
