"""COST-05 cost-ledger test stubs — Phase 4 Wave 0.

Covers:
  - llm_cost event is written per researcher query() call
  - cost_usd payload field is a Decimal (not float)
  - normalize_decimals is called before append_event
  - ResultMessage(total_cost_usd=None) defaults to Decimal("0")

These tests import existing symbols (append_event, normalize_decimals,
ResultMessage from claude_agent_sdk.types) — they may partially collect.
Assertions that verify runtime behavior stub with NotImplementedError until
the cost-ledger write is wired into trigger_strategy_run in Wave 2.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import existing symbols (should not fail on collect)
# ---------------------------------------------------------------------------
from gekko.audit.log import append_event  # noqa: F401
from gekko.audit.canonical import normalize_decimals  # noqa: F401

# ---------------------------------------------------------------------------
# Import SDK ResultMessage — may fail if SDK version mismatch; that is OK for
# Wave 0 (the RED signal we need). If the SDK is present, it will import fine.
# ---------------------------------------------------------------------------
try:
    from claude_agent_sdk.types import ResultMessage  # type: ignore[import]
except ImportError:
    ResultMessage = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_cost_event_written_per_researcher_query() -> None:
    """One llm_cost audit event must be appended for each query() call in _run_researcher.

    The event must carry event_type="llm_cost" and payload["cost_usd"] == the
    Decimal value extracted from ResultMessage.total_cost_usd.
    """
    raise NotImplementedError(
        "stub — implement after cost-ledger write is wired into runtime.py in Wave 2"
    )


@pytest.mark.asyncio
async def test_cost_usd_is_decimal_not_float() -> None:
    """payload['cost_usd'] must be a Decimal instance, never a float.

    Float drift in money math is a Knight Capital class bug (PITFALLS Pitfall 1).
    The ledger write MUST pass through Decimal(str(total_cost_usd)) before
    calling normalize_decimals / append_event.
    """
    raise NotImplementedError(
        "stub — implement after cost-ledger write ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_normalize_decimals_called() -> None:
    """normalize_decimals must be called on the payload before append_event.

    This ensures trailing zeros are stripped so Decimal('0.050') and
    Decimal('0.05') produce identical canonical JSON (required for hash-chain
    integrity per Plan 01-04 decision).
    """
    raise NotImplementedError(
        "stub — implement after cost-ledger write ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_none_total_cost_usd_defaults_to_zero() -> None:
    """ResultMessage(total_cost_usd=None) → cost_usd payload field == Decimal('0').

    When the SDK does not populate total_cost_usd (e.g., an older SDK build or
    a mocked query that returns None), the ledger must store Decimal('0') rather
    than raising or storing 'None'.
    """
    # Quick sanity — Decimal(str(None or 0.0)) == Decimal("0") pattern works:
    cost_usd = Decimal(str(None or 0.0))
    assert cost_usd == Decimal("0")

    # The full integration (ResultMessage flowing into append_event) stubs here:
    raise NotImplementedError(
        "stub — wire full integration in Wave 2 after runtime.py cost-ledger write ships"
    )
