"""AST gate: expiry.py import chain must NOT import claude_agent_sdk or anthropic.

Deterministic Python firewall — prevents LLM-authored content from ever reaching
the sweep path. Plan 03-04 Task 1 per HITL-03 design constraint.
"""

from __future__ import annotations

from pathlib import Path


def test_expiry_no_sdk_import() -> None:
    """Verify ``src/gekko/approval/expiry.py`` has no claude_agent_sdk or anthropic import.

    Uses the simple-bytes-grep idiom from
    ``tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk``.
    """
    expiry_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "gekko"
        / "approval"
        / "expiry.py"
    )
    assert expiry_path.exists(), f"expiry.py not found at {expiry_path}"

    src_bytes = expiry_path.read_bytes()
    # Grep for the SDK package names as byte literals.
    assert b"claude_agent_sdk" not in src_bytes, (
        "expiry.py must NOT import claude_agent_sdk — deterministic Python firewall (HITL-03)"
    )
    assert b"anthropic" not in src_bytes, (
        "expiry.py must NOT import anthropic — deterministic Python firewall (HITL-03)"
    )
