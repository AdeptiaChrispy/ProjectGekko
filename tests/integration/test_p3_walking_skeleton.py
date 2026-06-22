"""Phase 3 walking-skeleton cassette — Plan 03-07 (Phase closure test).

Exercises every Phase-3 primitive in four end-to-end scenarios:

1. ``test_p3_happy_path_approve``
   PENDING proposal -> Slack approve (with dup-click dedup) -> executor ->
   fill -> daily P&L digest captures the fill.

2. ``test_p3_happy_path_with_edit_size``
   PENDING proposal -> Slack edit-size modal (within 2% drift) -> approve
   at new size -> executor -> fill.

3. ``test_dashboard_fallback``
   Slack is down; operator uses the dashboard:
   POST /login -> GET /approvals -> POST /approvals/{id}/approve ->
   executor fires -> fill -> audit chain intact.

4. ``test_expiry_chain``
   PENDING proposal with ``expires_at`` in the past ->
   ``expire_stale_proposals`` sweep -> EXPIRED + chat.update + expiry DM;
   then a late Slack approve click hits the expired proposal and
   place_order is never called.

Load-bearing assertions across all four tests:
  * ``walk_chain`` returns ``[]`` — SHA-256 audit-chain integrity preserved.
  * ``place_order`` called EXACTLY ONCE per execution test (never twice).
  * The dedup table records actions with the correct ``source`` values.

Cassette runs against an in-memory SQLCipher engine (``temp_sqlcipher_db``
fixture from conftest.py). No real Slack workspace, no real Alpaca account.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(
    *,
    user_id: str,
    decision_id: str,
    strategy_name: str = "ai-infra-bull",
    ticker: str = "NVDA",
    qty: Decimal = Decimal("5"),
    limit_price: Decimal = Decimal("1234.56"),
    client_order_id: str,
) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name=strategy_name,
        decision_id=decision_id,
        ticker=ticker,
        side="buy",
        qty=qty,
        target_notional_usd=qty * limit_price,
        order_type="limit",
        limit_price=limit_price,
        rationale="AI infrastructure thesis for P3 walking skeleton.",
        confidence=Decimal("0.80"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-17T10:00:00+00:00",
                summary="last $1234.56",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-17T10:00:00+00:00",
                summary="Q4 beat by 12%",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-17T10:00:00+00:00",
                summary="10-Q filed",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="AMD",
                why_rejected="Lower data-center exposure",
            ),
        ],
        client_order_id=client_order_id,
        account_mode="PAPER",
    )


async def _seed_chain_start(
    sf: Any,
    *,
    user_id: str,
    strategy_name: str = "ai-infra-bull",
    client_order_id: str,
    expires_at: str | None = None,
    slack_message_ts: str | None = "1234567890.000100",
    slack_message_channel: str | None = "D1234567890",
) -> tuple[str, str, TradeProposal]:
    """Seed User + Strategy + PENDING Proposal + initial 'proposal' event.

    Returns ``(proposal_id, strategy_id, tp)``.
    """
    strategy_id = "strat-p3-" + uuid4().hex[:8]
    proposal_id = uuid4().hex
    tp = _make_trade_proposal(
        user_id=user_id,
        decision_id=proposal_id,
        strategy_name=strategy_name,
        client_order_id=client_order_id,
    )
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
                expires_at=expires_at,
                slack_message_ts=slack_message_ts,
                slack_message_channel=slack_message_channel,
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


def _make_broker_mock(tp: TradeProposal, broker_order_id: str = "broker-p3-001") -> Any:
    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id=broker_order_id,
            client_order_id=tp.client_order_id,
            status="accepted",
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw={"id": broker_order_id, "status": "accepted"},
        )
    )
    return broker


def _make_task_tracker() -> tuple[list[Any], Any]:
    tasks: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _tracked(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        t = real_create_task(coro, **kwargs)
        tasks.append(t)
        return t

    return tasks, _tracked


async def _drain_tasks(tasks: list[Any]) -> None:
    while tasks:
        pending = tasks[:]
        tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Test 1: Slack approve + dup-click dedup + daily P&L
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_happy_path_approve(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: Slack approve (with dup-click) -> executor -> fill -> daily P&L."""
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler
    from gekko.audit import log as _audit_log
    from gekko.execution import executor
    from gekko.reporter import daily_pnl as _pnl_mod

    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "p3-approve-user"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    proposal_id, strategy_id, tp = await _seed_chain_start(
        sf,
        user_id=user_id,
        client_order_id="p3a" + "a" * 29,
    )

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_pnl_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = _make_broker_mock(tp, "broker-p3-approve-001")
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    ephemerals: list[str] = []

    async def _fake_ephemeral(response_url: str, text: str) -> None:
        ephemerals.append(text)

    monkeypatch.setattr(slack_handler, "_post_ephemeral", _fake_ephemeral)

    dms: list[str] = []

    async def _fake_dm(uid: str, text: str) -> None:
        dms.append(text)

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)
    monkeypatch.setattr(
        executor,
        "_send_slack_dm_respecting_quiet_hours",
        lambda uid, text, **kwargs: _fake_dm(uid, text),
    )

    # Mock NYSE calendar: non-empty so daily_pnl fires.
    import pandas as pd
    fake_schedule = pd.DataFrame(
        {
            "market_open": [pd.Timestamp("2026-06-17 09:30", tz="UTC")],
            "market_close": [pd.Timestamp("2026-06-17 16:00", tz="UTC")],
        }
    )
    import pandas_market_calendars as _mcal
    mock_nyse = MagicMock()
    mock_nyse.schedule.return_value = fake_schedule
    monkeypatch.setattr(_mcal, "get_calendar", lambda name: mock_nyse)

    # Capture Block Kit DMs from daily_pnl.
    captured_pnl_blocks: list[Any] = []

    async def _fake_pnl_send(
        uid: str, *, blocks: Any, category: str, fallback: str = ""
    ) -> None:
        captured_pnl_blocks.extend(
            blocks if isinstance(blocks, list) else [blocks]
        )

    monkeypatch.setattr(
        _pnl_mod, "_send_dm_blocks_respecting_quiet_hours", _fake_pnl_send
    )

    tasks, tracked_create_task = _make_task_tracker()
    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)

    # ---- First Slack approve click. -------------------------------------------
    approve_body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "headers": {},
        "response_url": "https://hooks.slack.com/actions/approve-url",
        "trigger_id": "trigger-001",
    }
    ack1 = AsyncMock()
    await slack_handler.handle_approve(ack=ack1, body=approve_body, client=None)
    ack1.assert_awaited()
    await _drain_tasks(tasks)

    broker.place_order.assert_awaited_once()

    # ---- TradingStream fill. --------------------------------------------------
    fill_payload = {
        "client_order_id": tp.client_order_id,
        "broker_order_id": "broker-p3-approve-001",
        "filled_qty": "5",
        "filled_avg_price": "1234.56",
        "ticker": "NVDA",
        "user_id": user_id,
        "event": "fill",
    }
    await executor.on_fill_event(fill_payload, user_id=user_id)

    # ---- Second Slack approve click (dup-click). ------------------------------
    ack2 = AsyncMock()
    await slack_handler.handle_approve(ack=ack2, body=approve_body, client=None)
    ack2.assert_awaited()
    await _drain_tasks(tasks)

    # place_order still exactly once.
    assert broker.place_order.await_count == 1, (
        f"place_order called {broker.place_order.await_count} times — double execution!"
    )
    assert len(ephemerals) >= 1, "Expected at least one ephemeral for the dup-click"

    # ---- Daily P&L digest. ---------------------------------------------------
    result = await _pnl_mod.send_daily_pnl_digest(user_id=user_id)
    assert result is True, "send_daily_pnl_digest should return True on a trading day"

    # ---- daily_pnl audit event. ----------------------------------------------
    async with sf() as session:
        events = (
            await session.execute(select(Event).order_by(Event.id.asc()))
        ).scalars().all()
    event_types = [e.event_type for e in events]
    assert "daily_pnl" in event_types, f"Expected 'daily_pnl' event; got {event_types}"

    # ---- P&L digest mentions the strategy + fills. ---------------------------
    assert len(captured_pnl_blocks) > 0, "Expected blocks in P&L digest"
    pnl_text = " ".join(
        (b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else "")
        for b in captured_pnl_blocks
        if isinstance(b, dict)
    )
    # The digest should reference either the strategy name or "1 fills".
    # (The strategy_name in the fill comes from the proposal payload, which
    # the executor logs as ai-infra-bull; but if the Strategy row payload_json
    # is "{}", the executor logs "_unknown_". Either way, fills_count=1.)
    assert "fills" in pnl_text.lower(), (
        f"P&L digest should mention fills; got: {pnl_text[:500]}"
    )

    # ---- Audit chain integrity. -----------------------------------------------
    async with sf() as session:
        broken = await walk_chain(session, user_id)
    assert broken == [], f"Audit chain broken: {broken}"

    # ---- Proposal ended FILLED. -----------------------------------------------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
    assert row.status == "FILLED", f"Expected FILLED; got {row.status}"


# ---------------------------------------------------------------------------
# Test 2: Edit-size branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_happy_path_with_edit_size(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edit-size (D-62, Plan 03-14): Slack Edit size button is a URL button that
    deep-links to the dashboard slider. The old views_open modal path is retired.

    This test verifies:
    1. handle_edit_size acks immediately and logs a warning (no views_open call).
    2. handle_edit_size_view_submission is a no-op ack stub.
    3. The dashboard POST /approvals/{id}/edit-submit path (tested in
       test_dashboard_edit_size_happy.py) remains the only active edit surface.
    4. Audit chain stays intact after the retired-stub calls.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "p3-editsize-user"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    proposal_id, strategy_id, tp = await _seed_chain_start(
        sf,
        user_id=user_id,
        client_order_id="p3e" + "b" * 29,
    )

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = _make_broker_mock(tp, "broker-p3-editsize-001")
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    dms: list[str] = []

    async def _fake_dm(uid: str, text: str) -> None:
        dms.append(text)

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)
    monkeypatch.setattr(
        executor,
        "_send_slack_dm_respecting_quiet_hours",
        lambda uid, text, **kwargs: _fake_dm(uid, text),
    )

    tasks, tracked_create_task = _make_task_tracker()
    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)

    # ---- D-62: handle_edit_size is a retired no-op ack stub. -----------------
    # URL buttons do NOT fire Bolt action callbacks. This tests that the stub
    # acks and does NOT call views_open (the old behavior is retired).
    views_open_calls: list[dict[str, Any]] = []
    mock_client = MagicMock()
    mock_client.views_open = AsyncMock(
        side_effect=lambda **kwargs: views_open_calls.append(kwargs)
    )

    edit_body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "trigger_id": "trigger-editsize-001",
        "response_url": "https://hooks.slack.com/actions/editsize-url",
        "headers": {},
    }
    ack_edit = AsyncMock()
    await slack_handler.handle_edit_size(ack=ack_edit, body=edit_body, client=mock_client)
    ack_edit.assert_awaited()

    # D-62: No views_open call — edit is now a URL deep-link to the dashboard.
    assert len(views_open_calls) == 0, (
        "handle_edit_size must NOT call views_open (D-62 retired); got "
        f"{len(views_open_calls)} call(s)"
    )

    # ---- D-62: handle_edit_size_view_submission is also a retired no-op stub. -
    dummy_view = {
        "private_metadata": json.dumps({"decision_id": proposal_id}),
        "state": {"values": {}},
    }
    sub_body = {"user": {"id": user_id}, "headers": {}}
    ack_submit = AsyncMock()
    await slack_handler.handle_edit_size_view_submission(
        ack=ack_submit, body=sub_body, client=mock_client, view=dummy_view
    )
    ack_submit.assert_awaited()
    # No place_order should have been triggered by the retired stub.
    assert broker.place_order.await_count == 0, (
        "Retired handle_edit_size_view_submission must not trigger place_order"
    )

    # ---- Audit chain integrity after retired-stub calls. ----------------------
    async with sf() as session:
        broken = await walk_chain(session, user_id)
    assert broken == [], f"Audit chain broken: {broken}"

    # ---- Proposal stays PENDING — no state mutation from retired stubs. -------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
    assert row.status == "PENDING", (
        f"Retired stubs must not mutate proposal state; expected PENDING, got {row.status}"
    )


# ---------------------------------------------------------------------------
# Test 3: Dashboard fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_fallback(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashboard fallback: login -> POST /approvals/{id}/approve -> executor -> fill."""
    from gekko.approval import dedup as _dedup_mod
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "p3-dash-user"
    correct_pass = "p3-dashboard-test-pass"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    import gekko.vault.passphrase as _vault
    _vault.set_passphrase(correct_pass)

    proposal_id, strategy_id, tp = await _seed_chain_start(
        sf,
        user_id=user_id,
        client_order_id="p3d" + "c" * 29,
    )

    from gekko.dashboard import routes as _dash_routes
    monkeypatch.setattr(_dash_routes, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = _make_broker_mock(tp, "broker-p3-dash-001")
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    dms: list[str] = []

    async def _fake_dm(uid: str, text: str) -> None:
        dms.append(text)

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)
    monkeypatch.setattr(
        executor,
        "_send_slack_dm_respecting_quiet_hours",
        lambda uid, text, **kwargs: _fake_dm(uid, text),
    )

    tasks, tracked_create_task = _make_task_tracker()
    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)

    import httpx
    from gekko.dashboard.app import create_app

    app = create_app()
    app.state.scheduler = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        # Login.
        login_resp = await client.post(
            "/login",
            data={"passphrase": correct_pass, "next": "/approvals"},
        )
        assert login_resp.status_code == 303, (
            f"Login should 303-redirect; got {login_resp.status_code}: "
            f"{login_resp.text[:300]}"
        )

        # GET /approvals.
        approvals_resp = await client.get("/approvals")
        assert approvals_resp.status_code == 200, (
            f"GET /approvals should 200; got {approvals_resp.status_code}"
        )

        # POST /approvals/{id}/approve.
        approve_resp = await client.post(f"/approvals/{proposal_id}/approve")

    assert approve_resp.status_code == 200, (
        f"POST /approvals/{{id}}/approve should 200; got {approve_resp.status_code}"
    )

    await _drain_tasks(tasks)

    assert broker.place_order.await_count == 1, (
        f"place_order called {broker.place_order.await_count} times"
    )

    # Fill arrives.
    fill_payload = {
        "client_order_id": tp.client_order_id,
        "broker_order_id": "broker-p3-dash-001",
        "filled_qty": "5",
        "filled_avg_price": "1234.56",
        "ticker": "NVDA",
        "user_id": user_id,
        "event": "fill",
    }
    await executor.on_fill_event(fill_payload, user_id=user_id)

    # Dedup row source="dashboard".
    async with sf() as session:
        dedup_rows = (
            await session.execute(
                select(SlackActionDedup).where(
                    SlackActionDedup.proposal_id == proposal_id,
                    SlackActionDedup.action_id == "approve_proposal",
                )
            )
        ).scalars().all()
    assert len(dedup_rows) >= 1, "Expected a dedup row for dashboard approve"
    assert any(r.source == "dashboard" for r in dedup_rows), (
        f"Expected source='dashboard'; got {[r.source for r in dedup_rows]}"
    )

    # Audit chain integrity.
    async with sf() as session:
        broken = await walk_chain(session, user_id)
    assert broken == [], f"Audit chain broken: {broken}"

    # Proposal FILLED.
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
    assert row.status == "FILLED", f"Expected FILLED; got {row.status}"

    _vault.clear()


# ---------------------------------------------------------------------------
# Test 4: Expiry chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_chain(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expiry: PENDING past expires_at -> sweep -> EXPIRED + chat.update + DM.

    Then a late Slack approve click finds the proposal EXPIRED and place_order
    is never called.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import expiry as _expiry_mod
    from gekko.approval import slack_handler
    from gekko.audit import log as _audit_log
    from gekko.execution import executor

    _audit_log._append_locks.clear()

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "p3-expiry-user"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    # Seed proposal with expires_at 5 minutes in the past.
    past_expires = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    proposal_id, strategy_id, tp = await _seed_chain_start(
        sf,
        user_id=user_id,
        client_order_id="p3x" + "e" * 29,
        expires_at=past_expires,
        slack_message_ts="1234567890.000200",
        slack_message_channel="D_EXPIRY_CHAN",
    )

    monkeypatch.setattr(_expiry_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    # Broker must NOT be called on an expired proposal.
    broker = _make_broker_mock(tp, "broker-p3-expiry-SHOULD-NOT-BE-CALLED")
    monkeypatch.setattr(executor, "_build_broker", lambda *a, **k: broker)

    chat_updates: list[dict[str, Any]] = []
    expiry_dms: list[str] = []
    ephemerals: list[str] = []

    from gekko.slack.app import slack_app
    slack_app.client.chat_update = AsyncMock(
        side_effect=lambda **kwargs: chat_updates.append(kwargs)
    )

    async def _fake_respecting_qh(uid: str, text: str, **kwargs: Any) -> None:
        expiry_dms.append(text)

    monkeypatch.setattr(executor, "_send_slack_dm_respecting_quiet_hours", _fake_respecting_qh)
    monkeypatch.setattr(executor, "_send_slack_dm", lambda uid, text: expiry_dms.append(text))

    async def _fake_ephemeral(response_url: str, text: str) -> None:
        ephemerals.append(text)

    monkeypatch.setattr(slack_handler, "_post_ephemeral", _fake_ephemeral)

    tasks, tracked_create_task = _make_task_tracker()
    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)

    # ---- Run the sweep. -------------------------------------------------------
    expired_count = await _expiry_mod.expire_stale_proposals(user_id=user_id)
    assert expired_count == 1, f"Expected 1 proposal expired; got {expired_count}"

    # ---- Proposal EXPIRED + expiration event. ---------------------------------
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
    assert row.status == "EXPIRED", f"Expected EXPIRED; got {row.status}"

    async with sf() as session:
        events = (
            await session.execute(select(Event).order_by(Event.id.asc()))
        ).scalars().all()
    event_types = [e.event_type for e in events]
    assert "expiration" in event_types, f"Expected 'expiration' event; got {event_types}"

    # Verify expiration event payload.
    expiry_evt = next(e for e in events if e.event_type == "expiration")
    outer = json.loads(expiry_evt.payload_json)
    payload = outer.get("payload", outer)
    assert payload.get("reason") == "timeout", (
        f"expiration event reason should be 'timeout'; got {payload.get('reason')}"
    )

    # ---- chat.update called with expired-card blocks. -------------------------
    assert len(chat_updates) >= 1, "Expected chat.update to be called"
    chat_update_call = chat_updates[0]
    assert chat_update_call.get("ts") == "1234567890.000200", (
        f"chat.update ts mismatch; got {chat_update_call.get('ts')}"
    )
    blocks_text = json.dumps(chat_update_call.get("blocks", []))
    assert "EXPIRED" in blocks_text or "expired" in blocks_text.lower(), (
        f"chat.update blocks should contain EXPIRED marker; got: {blocks_text[:500]}"
    )

    # ---- Expiry DM fired. -----------------------------------------------------
    assert len(expiry_dms) >= 1, "Expected at least one expiry DM"
    combined_dms = " ".join(expiry_dms)
    assert "expired" in combined_dms.lower() or "timeout" in combined_dms.lower(), (
        f"Expiry DM should mention expiry/timeout; got: {combined_dms[:300]}"
    )

    # ---- Late Slack approve on EXPIRED proposal. ------------------------------
    approve_body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "headers": {},
        "response_url": "https://hooks.slack.com/actions/late-approve-url",
        "trigger_id": "trigger-late-001",
    }
    ack_late = AsyncMock()
    await slack_handler.handle_approve(ack=ack_late, body=approve_body, client=None)
    ack_late.assert_awaited()
    await _drain_tasks(tasks)

    # ---- place_order NEVER called. --------------------------------------------
    assert broker.place_order.await_count == 0, (
        f"place_order should NOT be called on an expired proposal; "
        f"got {broker.place_order.await_count} calls"
    )

    # Final status still EXPIRED.
    async with sf() as session:
        row_after = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
    assert row_after.status == "EXPIRED", (
        f"Proposal should remain EXPIRED after late approve; got {row_after.status}"
    )

    # ---- Audit chain integrity. -----------------------------------------------
    async with sf() as session:
        broken = await walk_chain(session, user_id)
    assert broken == [], f"Audit chain broken: {broken}"
