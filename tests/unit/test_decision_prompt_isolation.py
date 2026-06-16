"""Wave-0 stub — RES-06 Decision prompt isolation from raw Researcher transcript.

# WAVE-0 STUB: owned by plan 02-04 — DO NOT delete the skip until that plan's tasks land

Covers RES-06 — the Decision subagent receives ONLY the parsed
ResearchBrief JSON, not the Researcher's raw tool transcript. Defends
against prompt-injection from untrusted news article bodies surfacing in
the Decision agent's context window.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_decision_prompt_contains_no_researcher_transcript_placeholder() -> None:
    """Will assert the second query()'s prompt is parsed ResearchBrief only."""
    pass
