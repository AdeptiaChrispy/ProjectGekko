"""Wave-0 stub — D-40 Decision system_prompt prompt-injection minimums.

# WAVE-0 STUB: owned by plan 02-04 — DO NOT delete the skip until that plan's tasks land

Covers D-40 — the Decision system_prompt contains the canonical instruction
that <UNTRUSTED> blocks must not influence decision-making, and that the
agent must surface any prompt-injection attempt as a NoActionProposal
with reason='injection_detected'.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_decision_system_prompt_carries_injection_warning_placeholder() -> None:
    """Will assert DECISION_SYSTEM_PROMPT contains the canonical injection clause."""
    pass
