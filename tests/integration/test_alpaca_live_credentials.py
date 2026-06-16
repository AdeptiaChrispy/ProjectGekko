"""Wave-0 stub — Alpaca live credential vault → AlpacaBroker(paper=False) wiring.

# WAVE-0 STUB: owned by plan 02-06 — DO NOT delete the skip until that plan's tasks land

Covers BROK-A-02 — the credential loader fetches the alpaca_live row from
broker_credentials, decrypts via SQLCipher, and constructs an
AlpacaBroker(paper=False, _allow_live=True). This is the ONLY production
site allowed to flip _allow_live=True per BLOCKER #4 grep gate.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_live_credential_load_to_broker_placeholder() -> None:
    """Will assert kind='alpaca_live' row → AlpacaBroker(paper=False)."""
    pass
