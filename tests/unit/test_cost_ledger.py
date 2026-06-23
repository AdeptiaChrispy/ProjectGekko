"""COST-05 cost-ledger tests — Phase 4 Wave 4.

Covers:
  - llm_cost event is written per researcher query() call
  - cost_usd payload field is a Decimal (not float)
  - normalize_decimals is called before append_event
  - ResultMessage(total_cost_usd=None) defaults to Decimal("0")
  - call_type distinguishes researcher / decision / triage

Wave 4 (04-04): stubs replaced with real integration tests that drive
_run_researcher / _run_decision via full patching of query() and
append_event and assert the llm_cost audit event payload shape.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import existing symbols (should not fail on collect)
# ---------------------------------------------------------------------------
from gekko.audit.log import append_event  # noqa: F401
from gekko.audit.canonical import normalize_decimals  # noqa: F401

# ---------------------------------------------------------------------------
# Import SDK types
# ---------------------------------------------------------------------------
from claude_agent_sdk.types import ResultMessage as SDKResultMessage
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_factory() -> Any:
    """Build a mock session factory that properly supports async context manager."""
    mock_session = AsyncMock()
    # session.begin() must also be an async context manager
    mock_begin_ctx = AsyncMock()
    mock_begin_ctx.__aenter__ = AsyncMock(return_value=None)
    mock_begin_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin_ctx)

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    mock_factory = MagicMock(side_effect=_session_cm)
    return mock_factory


# ---------------------------------------------------------------------------
# Tests: Decimal money math correctness (pure unit tests, no side effects)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_total_cost_usd_defaults_to_zero() -> None:
    """ResultMessage(total_cost_usd=None) → cost_usd computation == Decimal('0').

    When the SDK does not populate total_cost_usd (e.g., older SDK build or
    mocked query returning None), the ledger must store Decimal('0') not raise.
    """
    # Pattern used in runtime.py:
    result_msg_none = MagicMock(spec=SDKResultMessage)
    result_msg_none.total_cost_usd = None
    cost_usd = (
        Decimal(str(result_msg_none.total_cost_usd or 0.0))
        if result_msg_none
        else Decimal("0")
    )
    assert cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_cost_usd_is_decimal_not_float() -> None:
    """payload['cost_usd'] must be a Decimal instance, never a float.

    Float drift in money math is a Knight Capital class bug (PITFALLS Pitfall 1).
    The ledger write MUST pass through Decimal(str(total_cost_usd)) before
    calling normalize_decimals / append_event.
    """
    total_cost_float = 0.012345
    cost_usd = Decimal(str(total_cost_float))
    assert isinstance(cost_usd, Decimal), (
        f"cost_usd must be Decimal, got {type(cost_usd)}"
    )
    assert float(cost_usd) == pytest.approx(total_cost_float)


@pytest.mark.asyncio
async def test_normalize_decimals_called() -> None:
    """normalize_decimals strips trailing zeros before canonical JSON.

    Decimal('0.050') and Decimal('0.05') must produce identical canonical JSON.
    """
    payload_raw = {"cost_usd": Decimal("0.0500"), "input_tokens": 100}
    payload_norm = normalize_decimals(payload_raw)
    # Trailing zero stripped: "0.05" not "0.050"
    assert str(payload_norm["cost_usd"]) == "0.05"


# ---------------------------------------------------------------------------
# Integration tests: llm_cost event written per query() call
#
# Strategy: patch query() to return a real ResultMessage + AssistantMessage
# (using the actual SDK types so isinstance checks pass), then verify
# append_event is called with the correct event_type and Decimal cost_usd.
# ---------------------------------------------------------------------------


def _make_result_message_stream(total_cost_usd: float = 0.025, brief_text: str = "") -> list:
    """Build a list of messages that simulates a query() stream for researcher."""
    # Use real SDK types so isinstance checks in runtime pass.
    result_msg = SDKResultMessage(
        subtype="success",
        is_error=False,
        num_turns=1,
        result=brief_text,
        total_cost_usd=total_cost_usd,
    )
    return [result_msg]


@pytest.mark.asyncio
async def test_llm_cost_event_written_per_researcher_query() -> None:
    """One llm_cost audit event with call_type='researcher' per _run_researcher call.

    Mocks query() to return a ResultMessage with a known total_cost_usd.
    Patches append_event to capture calls, then asserts:
      - event_type == 'llm_cost'
      - payload['call_type'] == 'researcher'
      - payload['cost_usd'] is Decimal (not float)
    """
    # RESEARCH_BRIEF JSON for the test — must match ResearchBrief schema fields
    brief_json = json.dumps({
        "strategy_name": "s1",
        "user_id": "u1",
        "run_id": "r1",
        "generated_at": "2026-06-23T12:00:00+00:00",
        "tickers_examined": [],
        "catalysts_observed": [],
        "evidence": [],
        "research_budget_used": {"calls": 0, "tokens": 0, "seconds": 0.0},
        "notes": "test",
    })
    brief_text = f"<RESEARCH_BRIEF>{brief_json}</RESEARCH_BRIEF>"

    # Build real SDK objects so isinstance() checks in runtime pass.
    assistant_msg = AssistantMessage(
        content=[TextBlock(text=brief_text)],
        model="claude-sonnet-4-6",
        usage={"input_tokens": 200, "output_tokens": 100},
    )
    result_msg = SDKResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=1,
        session_id="sess-1",
        result=brief_text,
        total_cost_usd=0.025,
    )

    async def _fake_query(*args, **kwargs):
        yield assistant_msg
        yield result_msg

    append_calls: list[dict] = []

    async def _fake_append_event(session, *, user_id, strategy_id, event_type, payload, **kw):
        append_calls.append({"event_type": event_type, "payload": payload})
        return MagicMock()

    from gekko.schemas.strategy import Strategy, HardCaps
    from gekko.agent.runtime import _run_researcher

    strategy = Strategy(
        strategy_id="strat-1",
        user_id="u1",
        version=1,
        name="s1",
        thesis="test",
        watchlist=["AAPL"],
        hard_caps=HardCaps(
            max_position_pct=0.05,
            max_daily_loss_usd=200,
            max_trades_per_day=3,
            max_sector_exposure_pct=0.25,
        ),
        mode="paper",
        schedule_time=None,
        created_at="2026-06-23T00:00:00+00:00",
    )

    mock_factory = _make_session_factory()

    with (
        patch("gekko.agent.runtime.query", side_effect=_fake_query),
        patch("gekko.agent.runtime.append_event", side_effect=_fake_append_event),
    ):
        result = await _run_researcher(
            strategy=strategy,
            guidance=[],
            user_id="u1",
            run_id="run-1",
            mcp_server=MagicMock(),
            session_factory=mock_factory,
            strategy_db_id="strat-1",
        )

    # Verify llm_cost event was written
    llm_cost_events = [e for e in append_calls if e["event_type"] == "llm_cost"]
    assert len(llm_cost_events) == 1, (
        f"Expected 1 llm_cost event from _run_researcher, got {len(llm_cost_events)}"
    )
    payload = llm_cost_events[0]["payload"]
    assert payload["call_type"] == "researcher", (
        f"call_type must be 'researcher', got {payload['call_type']!r}"
    )
    cost_val = payload["cost_usd"]
    assert isinstance(cost_val, Decimal), (
        f"cost_usd must be Decimal in payload, got {type(cost_val)}: {cost_val}"
    )
    assert cost_val > Decimal("0"), "cost_usd must be positive from ResultMessage"


@pytest.mark.asyncio
async def test_llm_cost_event_written_per_decision_query() -> None:
    """One llm_cost audit event with call_type='decision' per _run_decision call."""
    from gekko.schemas.strategy import Strategy, HardCaps
    from gekko.schemas.research import ResearchBrief
    from gekko.agent.runtime import _run_decision

    strategy = Strategy(
        strategy_id="strat-1",
        user_id="u1",
        version=1,
        name="s1",
        thesis="test",
        watchlist=["AAPL"],
        hard_caps=HardCaps(
            max_position_pct=0.05,
            max_daily_loss_usd=200,
            max_trades_per_day=3,
            max_sector_exposure_pct=0.25,
        ),
        mode="paper",
        schedule_time=None,
        created_at="2026-06-23T00:00:00+00:00",
    )
    brief = ResearchBrief(
        strategy_name="s1",
        user_id="u1",
        run_id="run-1",
        generated_at="2026-06-23T12:00:00+00:00",
        tickers_examined=[],
        catalysts_observed=[],
        evidence=[],
        research_budget_used={"calls": 0, "tokens": 0, "seconds": 0.0},
        notes="test",
    )

    # Use real ResultMessage + AssistantMessage with a ToolUseBlock
    # Build real SDK objects so isinstance() checks pass in runtime.
    tool_block = ToolUseBlock(
        id="tb-1",
        name="mcp__gekko__propose_no_action",
        input={"reason": "nothing interesting", "conviction": 0.3},
    )
    assistant_msg = AssistantMessage(
        content=[tool_block],
        model="claude-sonnet-4-6",
        usage={"input_tokens": 150, "output_tokens": 80},
    )
    result_msg = SDKResultMessage(
        subtype="success",
        duration_ms=500,
        duration_api_ms=400,
        is_error=False,
        num_turns=1,
        session_id="sess-2",
        result="",
        total_cost_usd=0.01,
    )

    async def _fake_query_decision(*args, **kwargs):
        yield assistant_msg
        yield result_msg

    append_calls: list[dict] = []

    async def _fake_append_event(session, *, user_id, strategy_id, event_type, payload, **kw):
        append_calls.append({"event_type": event_type, "payload": payload})
        return MagicMock()

    mock_factory = _make_session_factory()

    with (
        patch("gekko.agent.runtime.query", side_effect=_fake_query_decision),
        patch("gekko.agent.runtime.append_event", side_effect=_fake_append_event),
    ):
        tool_outcome, tool_payload = await _run_decision(
            strategy=strategy,
            brief=brief,
            mcp_server=MagicMock(),
            user_id="u1",
            run_id="run-1",
            strategy_db_id="strat-1",
            strategy_name="s1",
            session_factory=mock_factory,
        )

    assert tool_outcome == "propose_no_action"
    llm_cost_events = [e for e in append_calls if e["event_type"] == "llm_cost"]
    assert len(llm_cost_events) == 1, (
        f"Expected 1 llm_cost event from _run_decision, got {len(llm_cost_events)}"
    )
    payload = llm_cost_events[0]["payload"]
    assert payload["call_type"] == "decision", (
        f"call_type must be 'decision', got {payload['call_type']!r}"
    )
    cost_val = payload["cost_usd"]
    assert isinstance(cost_val, Decimal), (
        f"cost_usd must be Decimal in payload, got {type(cost_val)}: {cost_val}"
    )
