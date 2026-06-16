"""Wave-0 stub — EXEC-08 tenacity wrapper introspection.

# WAVE-0 STUB: owned by plan 02-03 — DO NOT delete the skip until that plan's tasks land

Verifies the tenacity backoff parameters on Alpaca GET endpoints — exponential
multiplier, max attempts, retried exception set. Driven by cassette
fixtures/cassettes/alpaca_429_rate_limit.yaml.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_tenacity_wrapper_parameters_placeholder() -> None:
    """Will introspect retry.__wrapped__ + retry.retry / retry.stop / retry.wait."""
    pass


def test_429_response_triggers_backoff_placeholder() -> None:
    """Will use respx + tenacity to assert backoff on 429."""
    pass
