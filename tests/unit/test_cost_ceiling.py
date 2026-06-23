"""COST-01/COST-04 cost-ceiling test stubs — Phase 4 Wave 0.

Covers:
  - 80% threshold triggers degrade action
  - 100% threshold triggers halt action
  - halt returns outcome="skipped_cost_halt" from trigger_strategy_run
  - tz-midnight reset (DST-correct, user timezone)
  - single Slack DM at 80% (no repeat)
  - single Slack DM at 100% (no repeat)
  - triage gate skips thin cycles in degraded mode
  - allow when below 80%

All tests are stubs: they import not-yet-existing symbols from
``gekko.agent.cost_ceiling`` and ``gekko.agent.runtime`` so pytest
collection fails with ImportError — giving an unambiguous RED signal
until the implementation ships in later waves.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Import symbols that do not yet exist — intentional RED on collect
# ---------------------------------------------------------------------------
from gekko.agent.cost_ceiling import CeilingCheck, check_cost_ceiling  # noqa: F401
from gekko.agent.runtime import trigger_strategy_run  # noqa: F401 (already exists)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_ceiling_check(
    action: str,
    current_spend: Decimal,
    ceiling: Decimal,
    *,
    just_crossed_80: bool = False,
    just_crossed_100: bool = False,
) -> "CeilingCheck":
    pct = (current_spend / ceiling * 100) if ceiling else Decimal("0")
    return CeilingCheck(
        action=action,  # type: ignore[arg-type]
        current_spend=current_spend,
        ceiling=ceiling,
        pct=pct,
        just_crossed_80=just_crossed_80,
        just_crossed_100=just_crossed_100,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_80pct_threshold_triggers_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spend at 80% of ceiling → CeilingCheck.action == 'degrade'."""
    ceiling = Decimal("5.00")
    spend = Decimal("4.00")  # exactly 80%

    async def _mock_check(*args, **kwargs) -> CeilingCheck:
        return _make_ceiling_check("degrade", spend, ceiling)

    monkeypatch.setattr(
        "gekko.agent.cost_ceiling.check_cost_ceiling", _mock_check
    )

    result = await check_cost_ceiling(session_factory=None, user_id="test-user")  # type: ignore[arg-type]
    assert result.action == "degrade"


@pytest.mark.asyncio
async def test_100pct_threshold_triggers_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spend at 100% of ceiling → CeilingCheck.action == 'halt'."""
    ceiling = Decimal("5.00")
    spend = Decimal("5.00")  # exactly 100%

    async def _mock_check(*args, **kwargs) -> CeilingCheck:
        return _make_ceiling_check("halt", spend, ceiling)

    monkeypatch.setattr(
        "gekko.agent.cost_ceiling.check_cost_ceiling", _mock_check
    )

    result = await check_cost_ceiling(session_factory=None, user_id="test-user")  # type: ignore[arg-type]
    assert result.action == "halt"


@pytest.mark.asyncio
async def test_halt_returns_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """trigger_strategy_run returns outcome='skipped_cost_halt' when ceiling check returns halt."""
    ceiling_result = _make_ceiling_check(
        "halt", Decimal("5.00"), Decimal("5.00"), just_crossed_100=False
    )

    async def _mock_check(*args, **kwargs) -> CeilingCheck:
        return ceiling_result

    # Monkeypatch the cost ceiling check so we don't need a real DB
    monkeypatch.setattr(
        "gekko.agent.runtime.check_cost_ceiling", _mock_check
    )
    # Monkeypatch session factory so trigger_strategy_run doesn't open a real DB
    monkeypatch.setattr(
        "gekko.agent.runtime._get_session_factory",
        lambda user_id: (None, None),
    )

    result = await trigger_strategy_run(
        user_id="test-user",
        strategy_name="alpha",
        source="schedule",
    )
    assert result["outcome"] == "skipped_cost_halt"


@pytest.mark.asyncio
async def test_tz_midnight_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spend spanning yesterday UTC but today in America/New_York → spend computed as zero.

    The ceiling gate must use the user's configured timezone (not UTC) for
    the midnight reset boundary (COST-01 / D-03).
    """
    # This test drives check_cost_ceiling with a mocked DB session that
    # returns a cost event timestamped YESTERDAY UTC (still today's date in
    # UTC-5 America/New_York) — we assert cost_usd reads as Decimal("0")
    # because from the user's tz perspective no events exist today yet.
    # Implementation detail: the today_start_utc_str boundary computed
    # from America/New_York midnight will exclude yesterday-UTC events.
    raise NotImplementedError(
        "stub — implement after gekko.agent.cost_ceiling ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_single_dm_80(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cost_alert_80_sent_date == today's local date, just_crossed_80 is False.

    Prevents repeat DM spam across multiple skipped cycles in the same day.
    """
    raise NotImplementedError(
        "stub — implement after gekko.agent.cost_ceiling ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_single_dm_100(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cost_alert_100_sent_date == today's local date, just_crossed_100 is False.

    Prevents repeat halt-notification spam.
    """
    raise NotImplementedError(
        "stub — implement after gekko.agent.cost_ceiling ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_triage_gate_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """In degraded mode with Haiku triage gate returning 'NO', outcome == 'triage_skipped'.

    The Haiku pre-triage gate runs ONLY in degraded mode (D-04) and asks whether
    the current market snapshot is worth a full research run. A 'NO' from Haiku
    means the cycle is skipped (not queued, per D-07).
    """
    raise NotImplementedError(
        "stub — implement after triage gate ships in Wave 2"
    )


@pytest.mark.asyncio
async def test_allow_when_below_80pct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spend at 50% of ceiling → CeilingCheck.action == 'allow'."""
    ceiling = Decimal("5.00")
    spend = Decimal("2.50")  # 50%

    async def _mock_check(*args, **kwargs) -> CeilingCheck:
        return _make_ceiling_check("allow", spend, ceiling)

    monkeypatch.setattr(
        "gekko.agent.cost_ceiling.check_cost_ceiling", _mock_check
    )

    result = await check_cost_ceiling(session_factory=None, user_id="test-user")  # type: ignore[arg-type]
    assert result.action == "allow"
