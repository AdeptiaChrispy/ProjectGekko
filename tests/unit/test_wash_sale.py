"""Wave-0 stub — wash-sale FLAG path (per CONTEXT decision: flag-only in v1).

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land

Covers EXEC-09 — when the proposal would trigger a wash-sale (loss <30d ago,
same security re-buy), OrderGuard FLAGS but does NOT block; flag surfaces in
the proposal payload + Slack card. Wash-sale BLOCK path is deferred to v2.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_wash_sale_flag_set_when_replay_window_open_placeholder() -> None:
    """Will assert tp.wash_sale_flag is populated, NOT raises OrderGuardRejected."""
    pass
