"""Integration test: trigger_strategy_run skips during quiet hours — Plan 03-03 Task 3.

Covers 4 cases per D-46:
  (a) source="schedule" + in-window → no proposal built (skipped)
  (b) source="schedule" + out-of-window → proposal IS built (normal)
  (c) source="manual" + in-window → proposal IS built (gate bypassed)
  (d) source="manual" + out-of-window → proposal IS built (normal)

The quiet-hours predicate is monkeypatched so we don't need a real DB user row.
The strategy/proposal pipeline is monkeypatched with lightweight fakes so the
test runs without the Claude Agent SDK or real Alpaca credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.db.models import Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.strategy import HardCaps, Strategy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(*, user_id: str, strategy_name: str, decision_id: str) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name=strategy_name,
        decision_id=decision_id,
        ticker="NVDA",
        side="buy",
        qty=Decimal("1"),
        target_notional_usd=Decimal("200.00"),
        order_type="market",
        limit_price=None,
        rationale="Quiet hours integration test proposal.",
        confidence=Decimal("0.70"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="last $200",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="beat by 5%",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="10-Q filed",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(description="AMD", why_rejected="Lower exposure"),
        ],
        client_order_id="c" * 32,
        account_mode="PAPER",
    )


def _make_strategy(user_id: str, strategy_name: str) -> Strategy:
    """Build a full Strategy Pydantic instance for seeding the DB."""
    return Strategy(
        strategy_id="strat-" + uuid4().hex,
        user_id=user_id,
        name=strategy_name,
        version=1,
        thesis="Quiet hours integration test strategy.",
        watchlist=["NVDA"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("250"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
    )


async def _seed_user_and_strategy(
    sf: Any,
    *,
    user_id: str,
    strategy_name: str,
) -> str:
    """Insert a minimal User + Strategy row; return strategy_id."""
    strategy = _make_strategy(user_id, strategy_name)
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=user_id,
                strategy_name=strategy_name,
                version=1,
                payload_json=strategy.model_dump_json(),
                created_at=now,
            )
        )
    return strategy.strategy_id


def _build_fake_researcher_result() -> Any:
    """Return a canned ResearchBrief for the fake Researcher phase."""
    from gekko.schemas.research import ResearchBrief

    return ResearchBrief(
        strategy_name="test-strategy",
        user_id="qh-test-user",
        run_id=uuid4().hex,
        generated_at=datetime.now(UTC).isoformat(),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-17T12:00:00+00:00",
                summary="last $200",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Helper: patch the full LLM / broker pipeline
# ---------------------------------------------------------------------------


def _patch_pipeline(
    user_id: str,
    strategy_name: str,
    sf: Any,
    engine: Any,
    resolve_quiet_hours_returns: bool,
) -> list[Any]:
    """Return a list of context-manager patches for the agent pipeline."""
    decision_id = uuid4().hex
    tp = _make_trade_proposal(
        user_id=user_id, strategy_name=strategy_name, decision_id=decision_id
    )

    # Fake write_proposal that records the proposal + returns tp.
    _proposals_built: list[TradeProposal] = []

    async def fake_write_proposal(session: Any, **kwargs: Any) -> TradeProposal:
        _proposals_built.append(tp)
        # Also append the proposal audit event so the chain is valid.
        await append_event(
            session,
            user_id=user_id,
            strategy_id=kwargs.get("strategy_db_id", "strat-fake"),
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
        return tp

    from unittest.mock import AsyncMock, patch as _patch

    patches = [
        _patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=resolve_quiet_hours_returns),
        ),
        _patch(
            "gekko.agent.runtime._get_session_factory",
            return_value=(sf, engine),
        ),
        _patch(
            "gekko.agent.runtime.get_async_engine",
            return_value=engine,
        ),
        _patch(
            "gekko.agent.runtime.make_session_factory",
            return_value=sf,
        ),
        _patch(
            "gekko.agent.runtime._run_researcher",
            new=AsyncMock(return_value=_build_fake_researcher_result()),
        ),
        _patch(
            "gekko.agent.runtime._run_decision",
            new=AsyncMock(return_value=("propose_trade", tp.model_dump(mode="python"))),
        ),
        _patch(
            "gekko.agent.runtime.write_proposal",
            new=AsyncMock(side_effect=fake_write_proposal),
        ),
        _patch(
            "gekko.agent.runtime._build_gekko_mcp_server",
            return_value=MagicMock(),
        ),
        _patch(
            "gekko.agent.runtime.set_tool_context",
        ),
    ]
    return patches, _proposals_built


# ---------------------------------------------------------------------------
# (a) schedule source + in-window → no proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_skips_in_quiet_hours(
    temp_sqlcipher_db: Any,
) -> None:
    """source='schedule' + in-window → returns early, no proposal built."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "qh-test-user"
    strategy_name = "test-strategy"
    sf = make_session_factory(temp_sqlcipher_db)

    await _seed_user_and_strategy(sf, user_id=user_id, strategy_name=strategy_name)

    proposals_built: list[Any] = []

    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=True),  # in window
        ),
        patch(
            "gekko.agent.runtime._run_researcher",
            new=AsyncMock(return_value=_build_fake_researcher_result()),
        ) as mock_researcher,
    ):
        result = await trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy_name,
            source="schedule",
            session_factory=sf,
        )

    # Should have returned early with skipped outcome.
    assert result.get("outcome") == "skipped_quiet_hours", f"Expected skip, got: {result}"
    # Researcher should NOT have been called (gate fires before pipeline).
    mock_researcher.assert_not_called()


# ---------------------------------------------------------------------------
# (b) schedule source + out-of-window → normal cycle (proposal built)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_runs_outside_quiet_hours(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source='schedule' + out-of-window → normal cycle proceeds."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "qh-test-user-b"
    strategy_name = "test-strategy"
    sf = make_session_factory(temp_sqlcipher_db)

    await _seed_user_and_strategy(sf, user_id=user_id, strategy_name=strategy_name)

    decision_id = uuid4().hex
    tp = _make_trade_proposal(
        user_id=user_id, strategy_name=strategy_name, decision_id=decision_id
    )

    async def fake_write_proposal(session: Any, **kwargs: Any) -> TradeProposal:
        await append_event(
            session,
            user_id=user_id,
            strategy_id=kwargs.get("strategy_db_id", "strat-fake"),
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
        return tp

    proposal_written: list[TradeProposal] = []

    original_write = fake_write_proposal

    async def tracking_write(session: Any, **kwargs: Any) -> TradeProposal:
        result = await original_write(session, **kwargs)
        proposal_written.append(result)
        return result

    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=AsyncMock(return_value=False),  # NOT in window
        ),
        patch(
            "gekko.agent.runtime._run_researcher",
            new=AsyncMock(return_value=_build_fake_researcher_result()),
        ),
        patch(
            "gekko.agent.runtime._run_decision",
            new=AsyncMock(return_value=("propose_trade", tp.model_dump(mode="python"))),
        ),
        patch(
            "gekko.agent.runtime.write_proposal",
            new=AsyncMock(side_effect=tracking_write),
        ),
        patch(
            "gekko.agent.runtime._build_gekko_mcp_server",
            return_value=MagicMock(),
        ),
        patch("gekko.agent.runtime.set_tool_context"),
        patch(
            "gekko.reporter.slack.post_run_result",
            new=AsyncMock(),
        ),
    ):
        result = await trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy_name,
            source="schedule",
            session_factory=sf,
        )

    # outcome is the tool_outcome returned by the Decision agent ("propose_trade" or "propose_no_action")
    assert result.get("outcome") == "propose_trade", f"Expected propose_trade outcome, got: {result}"
    assert len(proposal_written) == 1, f"Expected 1 proposal, got: {len(proposal_written)}"


# ---------------------------------------------------------------------------
# (c) manual source + in-window → proposal built (gate bypassed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_source_bypasses_quiet_hours(
    temp_sqlcipher_db: Any,
) -> None:
    """source='manual' + in-window → gate bypassed, cycle proceeds normally."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "qh-test-user-c"
    strategy_name = "test-strategy"
    sf = make_session_factory(temp_sqlcipher_db)

    await _seed_user_and_strategy(sf, user_id=user_id, strategy_name=strategy_name)

    decision_id = uuid4().hex
    tp = _make_trade_proposal(
        user_id=user_id, strategy_name=strategy_name, decision_id=decision_id
    )

    proposal_written: list[TradeProposal] = []

    async def tracking_write(session: Any, **kwargs: Any) -> TradeProposal:
        await append_event(
            session,
            user_id=user_id,
            strategy_id=kwargs.get("strategy_db_id", "strat-fake"),
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
        proposal_written.append(tp)
        return tp

    resolve_mock = AsyncMock(return_value=True)  # in window — but should be bypassed

    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=resolve_mock,
        ),
        patch(
            "gekko.agent.runtime._run_researcher",
            new=AsyncMock(return_value=_build_fake_researcher_result()),
        ),
        patch(
            "gekko.agent.runtime._run_decision",
            new=AsyncMock(return_value=("propose_trade", tp.model_dump(mode="python"))),
        ),
        patch(
            "gekko.agent.runtime.write_proposal",
            new=AsyncMock(side_effect=tracking_write),
        ),
        patch(
            "gekko.agent.runtime._build_gekko_mcp_server",
            return_value=MagicMock(),
        ),
        patch("gekko.agent.runtime.set_tool_context"),
        patch(
            "gekko.reporter.slack.post_run_result",
            new=AsyncMock(),
        ),
    ):
        result = await trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy_name,
            source="manual",  # manual → gate bypassed
            session_factory=sf,
        )

    # Gate must NOT have been called (source != "schedule").
    resolve_mock.assert_not_called()
    assert result.get("outcome") == "propose_trade", f"Expected propose_trade, got: {result}"
    assert len(proposal_written) == 1


# ---------------------------------------------------------------------------
# (d) manual source + out-of-window → proposal built (normal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_source_out_of_window_runs_normally(
    temp_sqlcipher_db: Any,
) -> None:
    """source='manual' + out-of-window → normal cycle (gate not consulted)."""
    from gekko.agent.runtime import trigger_strategy_run

    user_id = "qh-test-user-d"
    strategy_name = "test-strategy"
    sf = make_session_factory(temp_sqlcipher_db)

    await _seed_user_and_strategy(sf, user_id=user_id, strategy_name=strategy_name)

    decision_id = uuid4().hex
    tp = _make_trade_proposal(
        user_id=user_id, strategy_name=strategy_name, decision_id=decision_id
    )

    proposal_written: list[TradeProposal] = []

    async def tracking_write(session: Any, **kwargs: Any) -> TradeProposal:
        await append_event(
            session,
            user_id=user_id,
            strategy_id=kwargs.get("strategy_db_id", "strat-fake"),
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
        proposal_written.append(tp)
        return tp

    resolve_mock = AsyncMock(return_value=False)  # not in window

    with (
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            new=resolve_mock,
        ),
        patch(
            "gekko.agent.runtime._run_researcher",
            new=AsyncMock(return_value=_build_fake_researcher_result()),
        ),
        patch(
            "gekko.agent.runtime._run_decision",
            new=AsyncMock(return_value=("propose_trade", tp.model_dump(mode="python"))),
        ),
        patch(
            "gekko.agent.runtime.write_proposal",
            new=AsyncMock(side_effect=tracking_write),
        ),
        patch(
            "gekko.agent.runtime._build_gekko_mcp_server",
            return_value=MagicMock(),
        ),
        patch("gekko.agent.runtime.set_tool_context"),
        patch(
            "gekko.reporter.slack.post_run_result",
            new=AsyncMock(),
        ),
    ):
        result = await trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy_name,
            source="manual",  # manual — gate not consulted
            session_factory=sf,
        )

    # Gate must NOT have been called.
    resolve_mock.assert_not_called()
    assert result.get("outcome") == "propose_trade", f"Expected propose_trade, got: {result}"
    assert len(proposal_written) == 1
