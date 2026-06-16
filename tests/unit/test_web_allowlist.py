"""Wave-0 stub — RES-07 web allowlist host parsing.

# WAVE-0 STUB: owned by plan 02-04 — DO NOT delete the skip until that plan's tasks land

Covers RES-07 — the web_fetch tool's host parsing + allowlist check. Any
URL whose hostname is not in the static allowlist is refused at the tool
boundary BEFORE the HTTP request fires.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_web_fetch_refuses_disallowed_host_placeholder() -> None:
    """Will assert web_fetch('https://evil.example') raises before HTTP."""
    pass
