"""Full HITL -> Executor -> Fill chain — Plan 01-08 Task 4 integration test.

SKELETON Wave 1 gate item 9. The full chain that this test exercises:

    Slack approval click  ->  approve_proposal (PENDING -> APPROVED)
                          ->  execute_proposal (market-hours guard +
                              AlpacaBroker.place_order [MOCKED] +
                              order_submitted audit event + state
                              transition to EXECUTING)
                          ->  on_fill_event  (fill audit event +
                              EXECUTING -> FILLED + Slack DM)

After the chain runs we assert FIVE audit events live in the audit
log: ``proposal``, ``approval``, ``order_submitted``, ``fill`` (the
``decision`` event is the agent runtime's responsibility — Plan 01-07
integration tests cover that link; here we seed a PENDING Proposal row
directly so the chain starts at approval).

The SHA-256 hash chain is walked end-to-end via
:func:`gekko.audit.verify.walk_chain` to assert intact (zero broken
rows).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.brokers.base import OrderResult
from gekko.db.models import Event, Proposal as ProposalRow, Strategy as StrategyRow, User
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
        # Plan 02-01 Task 3: target_notional_usd (D-27) + account_mode (BLOCKER #5).
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
        client_order_id="b" * 32,
        account_mode="PAPER",
    )


async def _seed_chain_start(
    sf: Any, *, user_id: str
) -> tuple[str, str, str, TradeProposal]:
    """Seed User + Strategy + PENDING Proposal + the matching 'proposal' event.

    Returns ``(proposal_id, strategy_id, user_id, tp)``. The first audit
    event (``proposal``) is appended here so the chain starts at a
    well-formed prev_hash — mirroring what Plan 01-07's ProposalWriter
    does in production.
    """
    strategy_id = "strat-" + uuid4().hex
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
            )
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )
    return proposal_id, strategy_id, user_id, tp


@pytest.mark.asyncio
async def test_full_approval_to_fill_chain(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: Slack Approve click -> Executor -> Fill -> intact chain.

    Mocks the AlpacaBroker and the Slack DM transport. Everything else
    (state machine, audit log, hash chain) runs real.
    """
    from gekko.approval import slack_handler
    from gekko.execution import executor

    # Clear the audit-log's per-user lock dict; pytest-asyncio creates a
    # fresh event loop per test but the module-level _append_locks survives,
    # and stale asyncio.Lock instances from a prior loop's append_event
    # acquisitions can wedge the chain writer.
    from gekko.audit import log as _audit_log

    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"

    # Plan 01-09 user_id split: the slack_handler now uses
    # settings.slack_user_id for the cross-user check and
    # settings.gekko_user_id for DB ops. Make both point at the test
    # user_id so the chain stays consistent under one identity.
    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs

    _gs.cache_clear()

    proposal_id, strategy_id, _, tp = await _seed_chain_start(
        sf, user_id=user_id
    )

    # Both modules share the same session-factory accessor — patch in one
    # place each.
    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    # Mock the broker — place_order returns the persisted client_order_id.
    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id="broker-int-001",
            client_order_id=tp.client_order_id,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": "broker-int-001", "status": "accepted"},
        )
    )
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    # Capture Slack DMs from both the slack_handler (approve DM) and the
    # executor (fill confirmation DM).
    dms: list[str] = []

    async def fake_send_dm(_uid: str, msg: str) -> None:
        dms.append(msg)

    monkeypatch.setattr(executor, "_send_slack_dm", fake_send_dm)

    # Mock the slack_handler's bolt-client DM call.
    client = MagicMock()
    client.chat_postMessage = AsyncMock(
        side_effect=lambda **kwargs: dms.append(kwargs.get("text", ""))
    )

    # ---- 1. Slack Approve click. -------------------------------------------
    # Intercept ``asyncio.create_task`` so we can await every background
    # task before asserting on chain state. The slack_handler dispatches
    # _approve_workflow via create_task, which in turn dispatches
    # execute_proposal the same way — both must complete before the
    # ``order_submitted`` event lands on disk. Polling for status is
    # inherently flaky on Windows + SQLCipher (the chain can take
    # several hundred ms cold); explicit await is deterministic.
    import asyncio

    tasks: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _tracked_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        t = real_create_task(coro, **kwargs)
        tasks.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked_create_task)

    # Plan 01-09 user_id fix: body.user.id is the Slack identity; we set
    # SLACK_USER_ID to match user_id above so the cross-user check passes.
    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
    }
    ack = AsyncMock()
    await slack_handler.handle_approve(ack=ack, body=body, client=client)
    ack.assert_awaited()

    # Drain the task tree until no new tasks are spawned (each completing
    # task may schedule another — _approve_workflow -> execute_proposal).
    while tasks:
        pending = tasks[:]
        tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    broker.place_order.assert_awaited_once()

    # ---- 2. TradingStream fill arrives. ------------------------------------
    fill_payload = {
        "client_order_id": tp.client_order_id,
        "broker_order_id": "broker-int-001",
        "filled_qty": "5",
        "filled_avg_price": "1234.50",
        "ticker": "NVDA",
        "user_id": user_id,
        "event": "fill",
    }
    await executor.on_fill_event(fill_payload, user_id=user_id)

    # ---- 3. Assert final state + audit-log chain. --------------------------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(
                    ProposalRow.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        assert row.status == "FILLED"
        assert row.broker_order_id == "broker-int-001"

        events = (
            await session.execute(
                select(Event).order_by(Event.id.asc())
            )
        ).scalars().all()
        types = [e.event_type for e in events]
        assert types == [
            "proposal",
            "approval",
            "order_submitted",
            "fill",
        ], f"unexpected audit chain: {types}"

        # Walk the SHA-256 chain — must be intact (no breaks).
        broken = await walk_chain(session, user_id)
        assert broken == []

    # Slack DMs: at least an "Approved..." (from handler) + a "Paper order
    # filled..." (from executor's fill DM).
    joined = " | ".join(dms)
    assert "approved" in joined.lower()
    assert "filled" in joined.lower()
