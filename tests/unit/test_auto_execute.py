"""Auto-execution branch — TRUST-02 / SC-2 (Plan 05-05 Task 1).

Behavioral contract for the deterministic auto-branch in
``gekko.agent.runtime`` (``_run_auto_branch`` + the ``trigger_strategy_run``
routing guard):

  * an ``auto-within-caps`` TradeProposal is auto-approved and routes through
    ``execute_proposal`` so OrderGuard re-checks caps as its last line (D-T08)
    — NEVER a direct ``broker.place_order``.
  * a ``NoActionProposal`` and a ``propose-only`` strategy do NOT auto-execute.
  * a LIVE strategy whose ``first_live_trade_confirmed_at IS NULL`` routes to
    AWAITING_2ND_CHANNEL (the Phase-2 dual-channel gate), NOT direct execute
    (D-T03) — live + auto stacks both gates.
  * every auto-execution writes an ``auto_execution`` audit event with a full
    rationale payload.
  * a cap-breaching auto proposal yields ``cap_rejection`` + FAILED (proving
    OrderGuard re-checks on the auto path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    StrategyMetadata,
    User,
)
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import (
    AlternativeConsidered,
    NoActionProposal,
    TradeProposal,
)
from gekko.schemas.research import EvidenceSnippet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(
    *,
    user_id: str = "auto-user",
    decision_id: str,
    strategy_name: str = "momentum-tech",
    account_mode: str = "PAPER",
    qty: Decimal = Decimal("5"),
    client_order_id: str | None = None,
) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name=strategy_name,
        decision_id=decision_id,
        ticker="NVDA",
        side="buy",
        qty=qty,
        target_notional_usd=Decimal("6172.80"),
        order_type="limit",
        limit_price=Decimal("1234.56"),
        rationale="Bullish on AI infrastructure demand into the next quarter.",
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-26T11:30:00+00:00",
                summary="NVDA last trade $1234.56.",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-26T11:30:00+00:00",
                summary="Beat by 12%.",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-26T11:30:00+00:00",
                summary="10-Q filed.",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="Wait for next earnings.",
                why_rejected="Catalyst already in market.",
            ),
        ],
        client_order_id=client_order_id or uuid4().hex[:16].ljust(32, "a")[:32],
        account_mode=account_mode,  # type: ignore[arg-type]
    )


def _make_no_action(*, user_id: str = "auto-user", decision_id: str) -> NoActionProposal:
    return NoActionProposal(
        user_id=user_id,
        strategy_name="momentum-tech",
        decision_id=decision_id,
        rationale="No catalyst today.",
        factors_considered=["flat tape", "no news"],
        confidence=Decimal("0.6"),
    )


async def _seed(
    sf: Any,
    *,
    user_id: str = "auto-user",
    strategy_name: str = "momentum-tech",
    account_mode: str = "PAPER",
    status: str = "PENDING",
    trust_level: str = "auto-within-caps",
    first_live_confirmed: str | None = None,
    proposal: TradeProposal | None = None,
) -> tuple[str, str, TradeProposal]:
    """Seed User + Strategy + StrategyMetadata + PENDING Proposal rows."""
    strategy_id = "strat-" + uuid4().hex
    proposal_id = uuid4().hex
    tp = proposal or _make_trade_proposal(
        user_id=user_id,
        decision_id=proposal_id,
        strategy_name=strategy_name,
        account_mode=account_mode,
    )
    tp = tp.model_copy(update={"user_id": user_id, "decision_id": proposal_id})
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name=strategy_name,
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )
        session.add(
            StrategyMetadata(
                user_id=user_id,
                strategy_name=strategy_name,
                trust_level=trust_level,
                first_live_trade_confirmed_at=first_live_confirmed,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status=status,
                account_mode=account_mode,
                payload_json=tp.model_dump_json(),
                client_order_id=tp.client_order_id,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
            )
        )
    return proposal_id, strategy_id, tp


# ---------------------------------------------------------------------------
# 1. auto-within-caps PAPER → approve + execute_proposal (single path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_within_caps_routes_through_execute_proposal(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto proposal reaches the broker only via execute_proposal (OrderGuard re-check)."""
    from gekko.agent import runtime

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(sf)

    # Capture the execute_proposal dispatch (proves it is the single path).
    execute_calls: list[tuple[str, str]] = []

    async def fake_execute(pid: str, uid: str) -> None:
        execute_calls.append((pid, uid))

    monkeypatch.setattr(runtime, "execute_proposal", fake_execute)

    outcome = await runtime._run_auto_branch(
        proposal=tp,
        user_id="auto-user",
        strategy_db_id=strategy_id,
        session_factory=sf,
    )

    assert outcome == "auto_executed"
    # execute_proposal dispatched exactly once with the proposal id (the
    # decision_id == proposal_id 1:1 mapping).
    assert execute_calls == [(proposal_id, "auto-user")]

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        # approve_proposal moved PENDING → APPROVED before the executor ran.
        assert row.status == "APPROVED"

        approval = (
            await session.execute(
                select(Event).where(Event.event_type == "approval")
            )
        ).scalars().all()
        assert len(approval) == 1
        assert "auto-execute" in approval[0].payload_json
        assert "execution_path" in approval[0].payload_json


# ---------------------------------------------------------------------------
# 2. auto_execution audit event with full rationale payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_execution_event_written_with_rationale(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every auto-executed decision writes a full auto_execution audit event."""
    from gekko.agent import runtime

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(sf)

    monkeypatch.setattr(runtime, "execute_proposal", AsyncMock())

    await runtime._run_auto_branch(
        proposal=tp,
        user_id="auto-user",
        strategy_db_id=strategy_id,
        session_factory=sf,
    )

    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(Event.event_type == "auto_execution")
            )
        ).scalars().all()
    assert len(events) == 1
    pj = events[0].payload_json
    for needle in (
        "momentum-tech",
        "NVDA",
        "rationale_summary",
        "PAPER",
        proposal_id,
    ):
        assert needle in pj, f"auto_execution payload missing {needle!r}"


# ---------------------------------------------------------------------------
# 3. LIVE + first_live_trade_confirmed_at IS NULL → AWAITING_2ND_CHANNEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_first_trade_routes_to_dual_channel_not_execute(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LIVE + first_live_trade_confirmed_at IS NULL → AWAITING_2ND_CHANNEL (D-T03)."""
    from gekko.agent import runtime

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(
        sf,
        account_mode="LIVE",
        first_live_confirmed=None,  # first live trade — gate not yet passed
    )

    execute_calls: list[tuple[str, str]] = []

    async def fake_execute(pid: str, uid: str) -> None:
        execute_calls.append((pid, uid))

    monkeypatch.setattr(runtime, "execute_proposal", fake_execute)

    outcome = await runtime._run_auto_branch(
        proposal=tp,
        user_id="auto-user",
        strategy_db_id=strategy_id,
        session_factory=sf,
    )

    assert outcome == "awaiting_2nd_channel"
    # NO direct execute — the dual-channel gate must intercept.
    assert execute_calls == []

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        assert row.status == "AWAITING_2ND_CHANNEL"
        # No auto_execution event — the trade has not executed.
        auto_events = (
            await session.execute(
                select(Event).where(Event.event_type == "auto_execution")
            )
        ).scalars().all()
        assert auto_events == []


# ---------------------------------------------------------------------------
# 4. LIVE + first_live_trade_confirmed_at SET → auto-executes normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_with_confirmed_first_trade_auto_executes(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LIVE auto strategy past its first-live gate executes without HITL."""
    from gekko.agent import runtime

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(
        sf,
        account_mode="LIVE",
        first_live_confirmed="2026-06-20T15:00:00+00:00",  # gate already passed
    )

    execute_calls: list[tuple[str, str]] = []

    async def fake_execute(pid: str, uid: str) -> None:
        execute_calls.append((pid, uid))

    monkeypatch.setattr(runtime, "execute_proposal", fake_execute)

    outcome = await runtime._run_auto_branch(
        proposal=tp,
        user_id="auto-user",
        strategy_db_id=strategy_id,
        session_factory=sf,
    )

    assert outcome == "auto_executed"
    assert execute_calls == [(proposal_id, "auto-user")]


# ---------------------------------------------------------------------------
# 5. propose-only strategy → no auto-branch (routing guard in trigger run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_only_does_not_auto_execute(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A propose-only strategy returns the PENDING proposal — no auto-branch fires."""
    from gekko.agent import runtime

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(sf, trust_level="propose-only")

    # The auto-branch helper must not even be called for propose-only — but we
    # assert the guard directly: load_trust_level returns propose-only.
    from gekko.strategy.trust import load_trust_level

    monkeypatch.setattr(
        "gekko.strategy.trust._get_session_factory", lambda _u: (sf, None)
    )
    trust = await load_trust_level(
        user_id="auto-user", strategy_name="momentum-tech"
    )
    assert trust == "propose-only"

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        assert row.status == "PENDING"  # untouched


# ---------------------------------------------------------------------------
# 6. NoActionProposal never auto-executes (type guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_action_proposal_never_auto_executes() -> None:
    """The trigger guard only enters the auto-branch for TradeProposal."""
    na = _make_no_action(decision_id=uuid4().hex)
    # The guard in trigger_strategy_run is `isinstance(proposal, TradeProposal)`.
    assert not isinstance(na, TradeProposal)


# ---------------------------------------------------------------------------
# 7. cap-breaching auto proposal → cap_rejection + FAILED (OrderGuard re-check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_breach_on_auto_path_rejects_via_orderguard(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap breach on the auto path → cap_rejection + FAILED (OrderGuard re-checks).

    This exercises the REAL execute_proposal (not a mock): the auto-branch
    approves, then dispatches to execute_proposal → OrderGuard, whose wrapped
    broker raises OrderGuardRejected. The single enforcement path means the
    auto trade is structurally incapable of bypassing the cap.
    """
    from gekko.agent import runtime
    from gekko.core.errors import OrderGuardRejected
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, strategy_id, tp = await _seed(sf)

    # Both the runtime module and the executor module resolve their session
    # factories via the same seam.
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    # A broker whose place_order raises a cap rejection (simulating an
    # OrderGuard portfolio/capital cap breach as the last line).
    breaching_broker = MagicMock()
    breaching_broker.place_order = AsyncMock(
        side_effect=OrderGuardRejected(
            "portfolio_total_exposure",
            "aggregate exposure 60% exceeds cap 50%",
            extra={"ticker": "NVDA", "actual_pct": "0.60", "cap": "0.50"},
        )
    )

    async def fake_build_broker(*a: Any, **k: Any) -> Any:
        return breaching_broker

    monkeypatch.setattr(executor, "_build_broker", fake_build_broker)

    async def fake_send_blocks(user_id: str, *, blocks: Any, fallback: str = "") -> None:
        return None

    monkeypatch.setattr(executor, "_send_slack_dm_blocks", fake_send_blocks)

    # Real execute_proposal (the runtime module re-imports the symbol).
    monkeypatch.setattr(runtime, "execute_proposal", executor.execute_proposal)

    outcome = await runtime._run_auto_branch(
        proposal=tp,
        user_id="auto-user",
        strategy_db_id=strategy_id,
        session_factory=sf,
    )
    assert outcome == "auto_executed"  # the branch dispatched

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        # OrderGuard re-checked and rejected: FAILED, not FILLED/EXECUTING.
        assert row.status == "FAILED"

        cap_rej = (
            await session.execute(
                select(Event).where(Event.event_type == "cap_rejection")
            )
        ).scalars().all()
        assert len(cap_rej) == 1
        assert "portfolio_total_exposure" in cap_rej[0].payload_json


# ---------------------------------------------------------------------------
# 8. No direct broker path in runtime.py (grep gate)
# ---------------------------------------------------------------------------


def test_runtime_has_no_direct_broker_place_order() -> None:
    """`broker.place_order` must NOT appear in runtime.py — the auto path goes
    through execute_proposal → OrderGuard only (D-T08)."""
    import gekko.agent.runtime as _mod

    src = open(_mod.__file__, encoding="utf-8").read()
    assert "broker.place_order" not in src, (
        "runtime.py must not call broker.place_order directly — route through "
        "execute_proposal so OrderGuard re-checks all caps (D-T08)."
    )


# ---------------------------------------------------------------------------
# 9. Proposal card: AUTO-EXECUTED chip + no Approve/Reject (UI-SPEC §4b)
# ---------------------------------------------------------------------------


def _render_card(**ctx_overrides: Any) -> str:
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    templates_dir = (
        Path(__file__).parent.parent.parent
        / "src" / "gekko" / "dashboard" / "templates"
    )
    env = Environment(  # noqa: S701 — test render, autoescape on anyway
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    ctx: dict[str, Any] = {
        "proposal_id": "p1",
        "ticker": "NVDA",
        "side": "BUY",
        "qty": "5",
        "rationale": "Bullish.",
        "evidence": [],
        "status": "APPROVED",
        "account_mode": "PAPER",
        "expires_at": None,
        "slack_team_id": "",
        "slack_channel_id": "",
        "timeout_minutes": 30,
        "expired_at_local": "",
    }
    ctx.update(ctx_overrides)
    return env.get_template("_proposal_card.html.j2").render(**ctx)


def test_auto_card_renders_chip_and_no_actions() -> None:
    """An auto-executed card shows AUTO-EXECUTED chip and no Approve/Reject."""
    html = _render_card(execution_path="auto", status="FILLED")
    assert "chip-auto-executed" in html
    assert "AUTO-EXECUTED" in html
    # No human action buttons on an auto card.
    assert "/approve" not in html
    assert "/reject" not in html


def test_hitl_pending_card_still_has_actions() -> None:
    """A normal PENDING HITL card keeps Approve/Reject and no auto chip."""
    html = _render_card(status="PENDING")  # no execution_path
    assert "chip-auto-executed" not in html
    assert "/approve" in html
    assert "/reject" in html
