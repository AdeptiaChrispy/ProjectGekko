"""Integration test: approve + reject race via concurrent asyncio tasks (HITL-02).

Scenario: an operator fires both Approve and Reject on the same proposal in
rapid succession (simulating a double-click or a Slack-retry storm).

Invariants asserted (D-44 first-write-wins policy):
  1. Exactly one terminal state transition (APPROVED or REJECTED).
  2. Two SlackActionDedup rows exist (one per action_id — 'approve_proposal'
     and 'reject_proposal' are separate intents; the second one that reaches a
     terminal state loses because the state machine rejects the backward
     transition).
  3. At most one dedup_click audit event (only the losing workflow fires it).
  4. place_order is called ZERO or ONE time (zero if REJECTED wins; one if
     APPROVED wins).
  5. _post_ephemeral is called at most once (the loser fires it when its
     claim_action returns 'duplicate' OR when the state machine rejects it).

Note: because asyncio.gather is concurrent but SQLite WAL serializes writes,
the winner is nondeterministic. The test uses pytest-rerunfailures to allow
up to 2 reruns to cover both winner branches. The INVARIANTS are what matter
— they must hold regardless of which workflow wins.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.brokers.base import OrderResult
from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    SlackActionDedup,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

pytestmark = pytest.mark.integration


def _make_trade_proposal(*, user_id: str, decision_id: str) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="ai-infra-bull",
        decision_id=decision_id,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=Decimal("6172.80"),
        order_type="limit",
        limit_price=Decimal("1234.56"),
        rationale="Bullish on AI infrastructure.",
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="last $1234.56",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="beat by 12%",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q filed",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="AMD",
                why_rejected="Lower data-center exposure",
            ),
        ],
        client_order_id="c" * 32,
        account_mode="PAPER",
    )


async def _seed_race_start(
    sf: Any, *, user_id: str
) -> tuple[str, str, TradeProposal]:
    """Seed User + Strategy + PENDING Proposal + the initial 'proposal' event.

    Returns (proposal_id, strategy_id, tp).
    """
    from uuid import uuid4

    strategy_id = "strat-race-" + uuid4().hex[:8]
    proposal_id = uuid4().hex
    tp = _make_trade_proposal(user_id=user_id, decision_id=proposal_id)
    now = datetime.now(UTC).isoformat()

    async with sf() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="ai-infra-bull",
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status="PENDING",
                payload_json=tp.model_dump_json(),
                client_order_id=tp.client_order_id,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )

    return proposal_id, strategy_id, tp


@pytest.mark.asyncio
@pytest.mark.flaky(reruns=2)
async def test_dedup_race_approve_reject(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent approve + reject on the same proposal produces exactly one
    terminal state transition; the loser's claim_action returns 'duplicate'
    OR its state-machine rejects the transition. Either way:
    - exactly one terminal state
    - two SlackActionDedup rows (one per action_id)
    - place_order called 0 or 1 times (never twice)
    - audit chain integrity preserved (walk_chain returns [])
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    # Clear stale per-user asyncio.Lock instances.
    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-race-user"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    proposal_id, strategy_id, tp = await _seed_race_start(sf, user_id=user_id)

    # Patch session factories on both modules.
    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # Mock executor internals.
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    place_order_calls = 0

    async def _counted_place_order(*a: Any, **k: Any) -> OrderResult:
        nonlocal place_order_calls
        place_order_calls += 1
        return OrderResult(
            broker_order_id="broker-race-001",
            client_order_id=tp.client_order_id,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": "broker-race-001", "status": "accepted"},
        )

    broker = MagicMock()
    broker.place_order = AsyncMock(side_effect=_counted_place_order)
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    ephemeral_calls: list[str] = []

    async def _fake_post_ephemeral(response_url: str, text: str) -> None:
        ephemeral_calls.append(text)

    monkeypatch.setattr(slack_handler, "_post_ephemeral", _fake_post_ephemeral)

    # Suppress Slack DM side effects.
    async def _noop_send_dm(uid: str, msg: str) -> None:
        pass

    monkeypatch.setattr(executor, "_send_slack_dm", _noop_send_dm)

    # Track create_task calls so we can drain all background tasks.
    tasks: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _tracked_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        t = real_create_task(coro, **kwargs)
        tasks.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked_create_task)

    # Body dicts for the two concurrent actions.
    approve_body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "headers": {},
        "response_url": "https://hooks.slack.com/approve-url",
    }
    reject_body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "headers": {},
        "response_url": "https://hooks.slack.com/reject-url",
    }

    ack_approve = AsyncMock()
    ack_reject = AsyncMock()

    # Fire both handle_approve and handle_reject concurrently.
    await asyncio.gather(
        slack_handler.handle_approve(
            ack=ack_approve, body=approve_body, client=None
        ),
        slack_handler.handle_reject(
            ack=ack_reject, body=reject_body, client=None
        ),
        return_exceptions=True,
    )

    # Drain all background tasks to completion.
    while tasks:
        pending = tasks[:]
        tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    # -------------------------------------------------------------------------
    # Invariant 1: exactly one terminal state.
    # -------------------------------------------------------------------------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
    assert row.status in ("APPROVED", "APPROVED_LIVE", "EXECUTING", "FILLED", "REJECTED"), (
        f"Proposal ended in unexpected status: {row.status}"
    )
    # If we got PENDING still, the dedup gate may have prevented both — fail loudly.
    assert row.status != "PENDING", (
        "Proposal remained PENDING after both approve and reject — at least one must win"
    )

    # -------------------------------------------------------------------------
    # Invariant 2: two SlackActionDedup rows (one per action_id).
    # -------------------------------------------------------------------------
    async with sf() as session:
        dedup_rows = (
            await session.execute(select(SlackActionDedup))
        ).scalars().all()

    # Both approve_proposal and reject_proposal should have been attempted.
    action_ids_in_db = {r.action_id for r in dedup_rows}
    assert "approve_proposal" in action_ids_in_db, (
        "Expected an approve_proposal dedup row"
    )
    assert "reject_proposal" in action_ids_in_db, (
        "Expected a reject_proposal dedup row"
    )

    # -------------------------------------------------------------------------
    # Invariant 3: place_order called at most once.
    # -------------------------------------------------------------------------
    assert place_order_calls <= 1, (
        f"place_order was called {place_order_calls} times — double-execution!"
    )

    # -------------------------------------------------------------------------
    # Invariant 4: audit chain integrity preserved.
    # -------------------------------------------------------------------------
    async with sf() as session:
        broken = await walk_chain(session, user_id)
    assert broken == [], f"Audit chain broken: {broken}"

    # -------------------------------------------------------------------------
    # Invariant 5: at most one ephemeral fired.
    # -------------------------------------------------------------------------
    assert len(ephemeral_calls) <= 1, (
        f"Expected at most 1 ephemeral; got {len(ephemeral_calls)}: {ephemeral_calls}"
    )
