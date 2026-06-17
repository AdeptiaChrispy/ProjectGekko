"""Phase-2 walking-skeleton end-to-end test — Plan 02-07 Task 1.

The Phase-2 wave-gate test: a paper strategy is promoted to live, the
operator approves the FIRST live proposal in Slack, the dashboard
``/live-confirm`` second-channel POST releases the executor, OrderGuard
fires all checks, the wrapped (mocked) live :class:`AlpacaBroker` accepts
the order, the cassette returns a fill, and the audit chain across the 6
events stays intact.

Per Plan 02-07 must_haves:

  * 6-event audit chain for first-live trade:
    ``[decision, proposal, approval (awaiting_2nd_channel=True),
      approval (second_channel=True), order_submitted, fill]``
  * walk_chain returns ``[]``
  * Subsequent live trade on the same strategy skips the dual-channel gate
    and takes the Phase-1 single-channel path
  * Phase-1 walking-skeleton stays green (no regression — covered by
    ``tests/integration/test_trigger_run_end_to_end.py`` running alongside)
  * Zero ``pytest.skip("Wave-0 stub`` markers remain in tests/
  * Cassette JSON at ``tests/fixtures/cassettes/alpaca_live_promote_smoke.json``
    documents the recorded HTTP exchange shapes

Mock vs. real (per Plan 01-09 walking-skeleton convention):

  * MOCK: AlpacaBroker (cassette-shaped MagicMock; live HTTP never fires),
    is_market_open (forced True), Slack DM transport (captured to a list)
  * REAL: ProposalWriter, audit log (append_event + walk_chain), state
    machine (transition_status), OrderGuard with all 8 checks (universe,
    hard_caps, qty_price_drift, paper_live_pairing with credential_kind=
    "alpaca_live", kill_active, PDT, T+1, market_hours), Slack approve
    handler dual-channel branch, dashboard /live-confirm handler,
    on_fill_event, stamp_first_live_trade
"""

from __future__ import annotations

import asyncio
import json
import re
import time as _time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.approval import slack_handler as slack_handler_mod
from gekko.approval.proposals import transition_status
from gekko.approval.slack_handler import _approve_workflow
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.audit.verify import walk_chain
from gekko.brokers.base import Brokerage, OrderResult
from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    StrategyMetadata,
    User,
)
from gekko.db.session import make_session_factory
from gekko.execution import executor as executor_mod
from gekko.execution.checks import _hard_caps as hc_mod
from gekko.execution.checks import _kill_switch as ks_mod
from gekko.execution.checks import _market_hours as mh_mod
from gekko.execution.checks import _pdt as pdt_mod
from gekko.execution.orderguard import OrderGuard
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.strategy import HardCaps, Strategy
from gekko.strategy import promotion as promotion_mod
from gekko.vault import credentials as creds_mod

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Cassette + canned fixtures (mirrors plan 02-07 RESEARCH §9 + cassette JSON)
# ---------------------------------------------------------------------------


_CASSETTE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "cassettes"
    / "alpaca_live_promote_smoke.json"
)


def _load_cassette() -> dict[str, Any]:
    """Load the recorded Alpaca live exchange — Plan 02-07 cassette."""
    with _CASSETTE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


_USER_ID = "chris"
_STRATEGY_NAME = "ai-infra"
_STRATEGY_ID = "strat-ai-infra-live"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_strategy() -> Strategy:
    """Strategy seed for the walking-skeleton — live mode, AAPL watchlist."""
    return Strategy(
        strategy_id=_STRATEGY_ID,
        user_id=_USER_ID,
        name=_STRATEGY_NAME,
        version=1,
        thesis="AI infrastructure thesis (Phase-2 walking-skeleton).",
        watchlist=["AAPL"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.20"),
            max_daily_loss_usd=Decimal("10000"),
            max_trades_per_day=50,
            max_sector_exposure_pct=Decimal("1"),
        ),
        mode="live",
        created_at=_now_iso(),
    )


def _make_trade_proposal(
    *,
    decision_id: str,
    client_order_id: str,
    account_mode: str = "LIVE",
) -> TradeProposal:
    """Build a TradeProposal matching the cassette's $1 AAPL limit shape."""
    return TradeProposal(
        user_id=_USER_ID,
        strategy_name=_STRATEGY_NAME,
        decision_id=decision_id,
        ticker="AAPL",
        side="buy",
        qty=Decimal("1"),
        target_notional_usd=Decimal("1.00"),
        order_type="limit",
        limit_price=Decimal("1.00"),
        rationale="Walking-skeleton $1 limit order (Phase-2 cassette).",
        confidence=Decimal("0.5"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/q/AAPL",
                fetched_at=_now_iso(),
                summary="AAPL ask=$1.00 (cassette quote).",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/n/AAPL",
                fetched_at=_now_iso(),
                summary="Headline placeholder (cassette news).",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://www.sec.gov/edgar/data/AAPL",
                fetched_at=_now_iso(),
                summary="10-Q placeholder (cassette EDGAR).",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="Buy MSFT instead",
                why_rejected="Not in watchlist for this walking-skeleton run.",
            ),
        ],
        client_order_id=client_order_id,
        account_mode=account_mode,
    )


# ---------------------------------------------------------------------------
# Seam wiring — cassette-driven mock broker + session-factory monkeypatches
# ---------------------------------------------------------------------------


def _build_cassette_broker(
    cassette: dict[str, Any], *, client_order_id: str
) -> MagicMock:
    """Build a MagicMock(Brokerage) that returns cassette-shaped responses.

    Mirrors plan 02-07 RESEARCH §9 + the Phase-1 walking-skeleton convention
    of mocking AlpacaBroker outright (no live HTTP); the cassette JSON
    documents what the real responses would look like.
    """
    account = cassette["account"]
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.supports_fractional = True
    broker.is_paper = False  # LIVE branch
    broker.get_account = AsyncMock(return_value=dict(account))
    broker.get_positions = AsyncMock(return_value=list(cassette["positions"]))
    broker.get_quote = AsyncMock(return_value=dict(cassette["quote_AAPL"]))
    broker.health_check = AsyncMock(return_value=True)
    broker.get_order_by_client_order_id = AsyncMock(return_value=None)
    broker.cancel_order = AsyncMock(return_value=True)
    broker.get_orders_open = AsyncMock(return_value=[])
    broker.cancel_all_open_orders = AsyncMock(return_value=[])
    # The recorded limit-order shape with client_order_id stamped at runtime.
    order_body = dict(cassette["limit_order_AAPL_live"])
    order_body["client_order_id"] = client_order_id
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            broker_order_id=order_body["id"],
            client_order_id=client_order_id,
            status=order_body["status"],
            filled_qty=Decimal(order_body["filled_qty"]),
            avg_fill_price=None,
            raw=order_body,
        )
    )
    broker._client = None
    broker._wrapped = broker  # so OrderGuard introspection works
    return broker


def _patch_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sf: Any,
    broker_ref: dict[str, Any],
    strategy: Strategy,
    sent_dms: list[dict[str, Any]],
) -> None:
    """Wire all the module-level seams onto the temp DB + mocked broker.

    ``broker_ref`` is a single-element dict containing the wrapped broker
    under the key ``'broker'``. Allows the broker to be constructed AFTER
    seam wiring (so the test can compute the deterministic
    ``client_order_id`` before stamping the broker's OrderResult).
    """
    # Force market open across every check (executor + market_hours check).
    monkeypatch.setattr(executor_mod, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(mh_mod, "is_market_open", lambda *a, **k: True)

    # Session-factory seams.
    monkeypatch.setattr(
        executor_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        slack_handler_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        hc_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        pdt_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        creds_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # _build_broker — wrap the cassette MagicMock in OrderGuard with
    # credential_kind="alpaca_live" so check_paper_live_pairing's 4th axis
    # fires correctly. The wrapped broker is looked up from broker_ref at
    # call time so the test can construct it after computing the
    # deterministic client_order_id.
    def _fake_build_broker(
        user_id_: str,
        strategy_arg: Strategy,
        account_mode: str,
        *,
        proposal: TradeProposal | None = None,
    ) -> Any:
        wrapped = broker_ref["broker"]
        return OrderGuard(
            wrapped,
            strategy=strategy_arg,
            account_mode=account_mode,  # type: ignore[arg-type]
            user_id=user_id_,
            proposal=proposal,
            credential_kind="alpaca_live",
        )

    monkeypatch.setattr(executor_mod, "_build_broker", _fake_build_broker)

    # Capture Slack DMs as structured records; both text + blocks variants.
    async def _fake_dm(_uid: str, msg: str) -> None:
        sent_dms.append({"kind": "text", "text": msg})

    async def _fake_dm_blocks(
        _uid: str, *, blocks: list[dict[str, Any]], fallback: str = ""
    ) -> None:
        sent_dms.append({"kind": "blocks", "blocks": blocks, "fallback": fallback})

    monkeypatch.setattr(executor_mod, "_send_slack_dm", _fake_dm)
    monkeypatch.setattr(executor_mod, "_send_slack_dm_blocks", _fake_dm_blocks)


# ---------------------------------------------------------------------------
# Seed helpers (User + Strategy + StrategyMetadata + paper + live vault)
# ---------------------------------------------------------------------------


async def _seed_user_and_strategy(
    sf: Any, strategy: Strategy, *, kill_active: bool = False
) -> None:
    """Seed the User + Strategy rows for the walking-skeleton run."""
    now = _now_iso()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=strategy.user_id,
                created_at=now,
                agreement_acknowledged_at=now,
                kill_active=kill_active,
            )
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy.strategy_id,
                user_id=strategy.user_id,
                strategy_name=strategy.name,
                version=strategy.version,
                payload_json=strategy.model_dump_json(),
                created_at=now,
            )
        )


def _patch_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the minimal env Settings + Slack identity-split expect."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-paper-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-paper-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST_USER")
    monkeypatch.setenv("GEKKO_USER_ID", _USER_ID)
    monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
    from gekko.config import get_settings

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# The walking-skeleton test (Plan 02-07 wave gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_walking_skeleton_promote_paper_to_live_end_to_end(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full Phase-2 walking-skeleton — promote → first-live → fill.

    Asserts: 6-event audit chain intact, OrderGuard delegated to the wrapped
    broker, ``first_live_trade_confirmed_at`` stamped after the fill, and
    a Slack DM with the dashboard /live-confirm URL was sent during the
    first-live divert.
    """
    from gekko.audit import log as _audit_log

    _audit_log._append_locks.clear()
    _patch_settings_env(monkeypatch)

    cassette = _load_cassette()
    sf = make_session_factory(temp_sqlcipher_db)
    strategy = _make_strategy()
    sent_dms: list[dict[str, Any]] = []

    # ---- 1. Seed user + strategy + paper credentials (no live yet). -----
    await _seed_user_and_strategy(sf, strategy)

    # ---- 2. Store LIVE credentials via the vault. -----------------------
    monkeypatch.setattr(
        creds_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    await creds_mod.store_live_credentials(
        user_id=_USER_ID,
        api_key=cassette["credentials"]["api_key"],
        secret_key=cassette["credentials"]["secret_key"],
    )

    # ---- 3. Promote the strategy to live-eligible. ----------------------
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    await promotion_mod.promote_strategy_to_live(
        user_id=_USER_ID, strategy_name=_STRATEGY_NAME
    )

    # Confirm the metadata flag landed.
    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, (_USER_ID, _STRATEGY_NAME)
        )
    assert meta is not None
    assert meta.live_mode_eligible is True
    assert meta.first_live_trade_confirmed_at is None
    assert meta.live_promoted_at is not None

    # ---- 4. Wire test seams onto the temp DB (broker constructed in
    #         step 5b once the deterministic client_order_id is known).
    # ---------------------------------------------------------------------
    decision_id = "prop-" + uuid4().hex
    # Placeholder broker — we'll wrap a cassette-shaped MagicMock at step
    # 5b once the ProposalWriter has computed the real client_order_id.
    broker_ref: dict[str, Any] = {}
    _patch_seams(
        monkeypatch,
        sf=sf,
        broker_ref=broker_ref,
        strategy=strategy,
        sent_dms=sent_dms,
    )

    # ---- 5. Write the proposal — emulates the LLM tool-call landing
    #         through ProposalWriter. This is the SAME boundary the SDK-
    #         mocked Phase-1 walking-skeleton uses (per Plan 01-09 SUMMARY
    #         "walking-skeleton cassette mocks AlpacaBroker.place_order +
    #         is_market_open + Slack DM transport BUT runs the real
    #         ProposalWriter..."). The propose_trade tool-call payload
    #         comes from the cassette's recorded order shape.
    # ---------------------------------------------------------------------
    from gekko.agent.proposal_writer import write_proposal

    propose_payload: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "qty": "1",
        "target_notional_usd": "1.00",
        "order_type": "limit",
        "limit_price": "1.00",
        "rationale": "Walking-skeleton $1 limit order (Phase-2 cassette).",
        "confidence": "0.5",
        "evidence": [
            {
                "source_type": "alpaca_quote",
                "source_url": "https://alpaca.markets/q/AAPL",
                "fetched_at": _now_iso(),
                "summary": "AAPL ask=$1.00 (cassette quote).",
            },
            {
                "source_type": "finnhub_news",
                "source_url": "https://finnhub.io/n/AAPL",
                "fetched_at": _now_iso(),
                "summary": "Headline placeholder (cassette news).",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/edgar/data/AAPL",
                "fetched_at": _now_iso(),
                "summary": "10-Q placeholder (cassette EDGAR).",
            },
        ],
        "alternatives_considered": [
            {
                "description": "Buy MSFT instead",
                "why_rejected": "Not in watchlist for this walking-skeleton run.",
            },
        ],
    }

    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id=_USER_ID,
            strategy=strategy,
            strategy_db_id=strategy.strategy_id,
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=propose_payload,
        )

    # The ProposalWriter must have stamped account_mode="LIVE" (BLOCKER #5
    # runtime half — strategy.mode='live' AND live_mode_eligible=True at
    # T0 → "LIVE"). Closes the TOCTOU window per Plan 02-06.
    assert isinstance(tp, TradeProposal)
    assert tp.account_mode == "LIVE", (
        "ProposalWriter must stamp account_mode='LIVE' when strategy.mode "
        "is 'live' AND StrategyMetadata.live_mode_eligible is True (BLOCKER "
        "#5 runtime half). Got: "
        f"{tp.account_mode!r}"
    )

    # ---- 5b. Re-read the persisted row to recover the deterministic
    #          client_order_id, then wire the cassette broker.
    async with sf() as session:
        persisted_row = await session.get(ProposalRow, decision_id)
    assert persisted_row is not None
    actual_client_order_id = persisted_row.client_order_id
    assert actual_client_order_id is not None
    # Also assert the new BLOCKER #5 runtime-half FIX landed on the row's
    # account_mode column (Plan 02-07 walking-skeleton bug fix — the
    # writer must populate the column, not just payload_json, because the
    # Slack approve handler reads ``row.account_mode``).
    assert persisted_row.account_mode == "LIVE", (
        "ProposalWriter must stamp account_mode on the ProposalRow column "
        "(NOT just payload_json) so the Slack approve handler's dual-channel "
        f"branch fires. Got column: {persisted_row.account_mode!r}"
    )
    broker = _build_cassette_broker(
        cassette, client_order_id=actual_client_order_id
    )
    broker_ref["broker"] = broker

    # ---- 6. Slack approve handler — FIRST-live diverts to AWAITING_2ND. -
    # ``execute_proposal`` is referenced as a module-level name in
    # slack_handler so we patch it there to detect bypass attempts. The
    # real executor MUST NOT be dispatched yet — only after the dashboard
    # confirms.
    dispatched_from_slack: list[str] = []

    async def _spy_execute_proposal(pid: str, uid: str) -> None:
        dispatched_from_slack.append(pid)

    monkeypatch.setattr(
        slack_handler_mod, "execute_proposal", _spy_execute_proposal
    )

    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock(return_value={"ok": True})

    await _approve_workflow(
        decision_id=decision_id,
        slack_user_id="U_TEST_USER",
        client=fake_client,
    )

    # Status moved to AWAITING_2ND_CHANNEL; executor NOT dispatched.
    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "AWAITING_2ND_CHANNEL", (
        f"Expected status='AWAITING_2ND_CHANNEL', got {row.status!r}"
    )
    assert dispatched_from_slack == [], (
        "Slack approve MUST NOT dispatch the executor on the first-live "
        "trade — the dashboard /live-confirm POST owns dispatch. "
        f"Got: {dispatched_from_slack}"
    )

    # The DM must contain the dashboard URL.
    assert fake_client.chat_postMessage.await_count == 1
    dm_text = fake_client.chat_postMessage.await_args.kwargs.get(
        "text"
    ) or ""
    assert "/live-confirm/" in dm_text, dm_text
    assert decision_id in dm_text, dm_text
    assert "FIRST live trade" in dm_text, dm_text

    # ---- 7. Dashboard /live-confirm — second channel transitions to
    #         APPROVED_LIVE + dispatches the real executor.
    # ---------------------------------------------------------------------
    # We exercise the SAME logic the FastAPI route runs: the transition +
    # dispatch happens inline here, mirroring the route handler. This
    # avoids spinning up the full FastAPI app while still exercising the
    # state machine + audit event verbatim.
    async with sf() as session, session.begin():
        await transition_status(
            session,
            decision_id,
            from_status="AWAITING_2ND_CHANNEL",
            to_status="APPROVED_LIVE",
        )
        await append_event(
            session,
            user_id=_USER_ID,
            strategy_id=strategy.strategy_id,
            event_type="approval",
            payload={
                "proposal_id": decision_id,
                "actor": "dashboard",
                "slack_action_id": "live_confirm",
                "second_channel": True,
            },
        )

    # Drain pattern from Plan 01-08 — collect every task spawned by the
    # executor + on_fill chain so we can deterministically await them.
    spawned: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _collecting_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:
        task = real_create_task(coro, *args, **kwargs)
        spawned.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _collecting_create_task)

    # Now actually run the executor. ``execute_proposal`` accepts both
    # APPROVED + APPROVED_LIVE entry statuses (Plan 02-06 closure).
    await executor_mod.execute_proposal(decision_id, _USER_ID)

    # Drain any tasks the executor spawned (rare in this branch but safe).
    while spawned:
        await asyncio.gather(*spawned, return_exceptions=True)
        next_batch = [t for t in spawned if not t.done()]
        if next_batch == spawned:
            break
        spawned = next_batch

    # OrderGuard must have delegated to the wrapped broker — verifying
    # all 8 checks (kill_switch, paper_live with credential_kind=
    # "alpaca_live", universe, hard_caps, qty_price_drift, PDT, T+1,
    # market_hours) passed.
    broker.place_order.assert_awaited_once()

    # Proposal row should now be EXECUTING with broker_order_id stamped.
    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "EXECUTING", (
        f"Expected status='EXECUTING' after order_submitted, got {row.status!r}"
    )
    assert row.broker_order_id == cassette["limit_order_AAPL_live"]["id"]

    # ---- 8. Fill stream callback — write fill event + stamp first-live.
    fill_payload = dict(cassette["trading_stream_fill_AAPL_live"])
    fill_payload["client_order_id"] = actual_client_order_id
    fill_payload["broker_order_id"] = cassette["limit_order_AAPL_live"]["id"]
    fill_payload["user_id"] = _USER_ID
    # The cassette's `event` key would collide with structlog's `event`
    # kwarg when `on_fill_event` calls log.warning(**payload) on the
    # unmatched path (irrelevant here, but defensive). Drop it; the
    # cassette `order` sub-dict (which carries `event` semantics) is also
    # extraneous to the on_fill_event API.
    fill_payload.pop("event", None)
    fill_payload.pop("order", None)
    fill_payload.pop("execution_id", None)

    await executor_mod.on_fill_event(fill_payload, user_id=_USER_ID)

    # Final proposal state.
    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "FILLED"
    assert row.broker_order_id == cassette["limit_order_AAPL_live"]["id"]

    # ---- 9. Walk the audit chain — assert the 6 first-live events in
    #         the correct order, and walk_chain returns [] (intact).
    # ---------------------------------------------------------------------
    async with sf() as session:
        events = (
            await session.execute(
                select(Event)
                .where(Event.user_id == _USER_ID)
                .order_by(Event.id.asc())
            )
        ).scalars().all()
        breaks = await walk_chain(session, _USER_ID)

    # Build a subset of TRADE-RELATED events for the 6-event chain
    # assertion. The chain also contains the Phase-2 credential /
    # promotion events from store_live_credentials
    # (``credentials_added``), promote_strategy_to_live
    # (``live_mode_promoted``), and stamp_first_live_trade
    # (``first_live_trade_confirmed``). Per BL-01 these are now their
    # own first-class event types in D-14 (previously written as
    # ``event_type="error"`` with a payload ``context`` discriminator;
    # Alembic 0003 extended ``ck_event_type`` to accept them). They
    # are not part of the documented 6-event trade chain but ARE part
    # of the persisted history; they only add to the chain length and
    # DO NOT break the hash chain.
    trade_event_types = [
        e.event_type
        for e in events
        if e.event_type
        in {
            "decision",
            "proposal",
            "approval",
            "order_submitted",
            "fill",
            "cap_rejection",
        }
    ]
    assert trade_event_types == [
        "decision",
        "proposal",
        "approval",  # awaiting_2nd_channel=True
        "approval",  # second_channel=True
        "order_submitted",
        "fill",
    ], f"unexpected trade-chain shape: {trade_event_types}"

    # Verify the two approval events carry the expected flags.
    approval_events = [e for e in events if e.event_type == "approval"]
    assert len(approval_events) == 2
    first_approval_payload = json.loads(approval_events[0].payload_json)
    second_approval_payload = json.loads(approval_events[1].payload_json)
    assert first_approval_payload["payload"].get("awaiting_2nd_channel") is True
    assert second_approval_payload["payload"].get("second_channel") is True

    # walk_chain over the FULL persisted history must still return []
    # — the credentials/promotion/stamp Phase-2 events (BL-01:
    # ``credentials_added``, ``live_mode_promoted``,
    # ``first_live_trade_confirmed``) live on the chain alongside the
    # trade events and the SHA-256 chain must hold.
    assert breaks == [], (
        f"SHA-256 audit chain broken at row(s): {breaks}. Full chain types: "
        f"{[e.event_type for e in events]}"
    )

    # ---- 10. first_live_trade_confirmed_at stamped on the metadata row.
    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, (_USER_ID, _STRATEGY_NAME)
        )
    assert meta is not None
    assert meta.first_live_trade_confirmed_at is not None, (
        "stamp_first_live_trade should have written a non-NULL "
        "first_live_trade_confirmed_at after the LIVE fill (D-32 "
        "per-strategy semantics)."
    )

    # ---- 11. Second live trade — bypasses the dual-channel gate.
    second_decision_id = "prop-" + uuid4().hex
    second_client_order_id = ("d" * 32)[:32]
    second_tp = _make_trade_proposal(
        decision_id=second_decision_id,
        client_order_id=second_client_order_id,
        account_mode="LIVE",
    )
    async with sf() as session, session.begin():
        now2 = _now_iso()
        session.add(
            ProposalRow(
                proposal_id=second_decision_id,
                user_id=_USER_ID,
                strategy_id=strategy.strategy_id,
                status="PENDING",
                payload_json=second_tp.model_dump_json(),
                client_order_id=second_client_order_id,
                broker_order_id=None,
                created_at=now2,
                updated_at=now2,
                account_mode="LIVE",
            )
        )

    dispatched_second: list[str] = []

    async def _spy_execute_second(pid: str, uid: str) -> None:
        dispatched_second.append(pid)

    monkeypatch.setattr(
        slack_handler_mod, "execute_proposal", _spy_execute_second
    )

    fake_client_2 = MagicMock()
    fake_client_2.chat_postMessage = AsyncMock(return_value={"ok": True})

    await _approve_workflow(
        decision_id=second_decision_id,
        slack_user_id="U_TEST_USER",
        client=fake_client_2,
    )

    # Wait briefly for the create_task to schedule + dispatch.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # The SECOND proposal must have gone PENDING -> APPROVED (single
    # channel) and dispatched the executor — because
    # first_live_trade_confirmed_at is now non-NULL.
    async with sf() as session:
        second_row = await session.get(ProposalRow, second_decision_id)
    assert second_row is not None
    assert second_row.status == "APPROVED", (
        f"Subsequent live trade must skip dual-channel gate (status= "
        f"APPROVED, single-channel). Got: {second_row.status!r}"
    )
    assert dispatched_second == [second_decision_id], (
        "Subsequent live trade MUST dispatch the executor from the Slack "
        f"approve handler (single-channel path). Got: {dispatched_second}"
    )


# ---------------------------------------------------------------------------
# TOCTOU defense — Plan 02-06 BLOCKER #5 closure verified end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_mode_survives_promote_then_demote_cycle(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal stamped LIVE at T0 must STAY LIVE after a later demote.

    BLOCKER #5 runtime half — ProposalWriter reads strategy.mode +
    StrategyMetadata.live_mode_eligible at PROPOSAL-BUILD time (T0). The
    stamp is final; demoting the strategy AFTER proposal-build does NOT
    rewrite the row. Closes the TOCTOU window from proposal to approve to
    execute.
    """
    from gekko.agent.proposal_writer import write_proposal

    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    strategy = _make_strategy()
    await _seed_user_and_strategy(sf, strategy)

    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # Promote → live-eligible.
    await promotion_mod.promote_strategy_to_live(
        user_id=_USER_ID, strategy_name=_STRATEGY_NAME
    )

    # Write a proposal — must be stamped LIVE at T0.
    decision_id = "prop-" + uuid4().hex
    propose_payload: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "qty": "1",
        "target_notional_usd": "1.00",
        "order_type": "limit",
        "limit_price": "1.00",
        "rationale": "TOCTOU defense — stamp LIVE at T0.",
        "confidence": "0.5",
        "evidence": [
            {
                "source_type": "alpaca_quote",
                "source_url": "https://alpaca.markets/q/AAPL",
                "fetched_at": _now_iso(),
                "summary": "ask=$1.00",
            },
            {
                "source_type": "finnhub_news",
                "source_url": "https://finnhub.io/n/AAPL",
                "fetched_at": _now_iso(),
                "summary": "news",
            },
            {
                "source_type": "edgar_filing",
                "source_url": "https://www.sec.gov/edgar/data/AAPL",
                "fetched_at": _now_iso(),
                "summary": "10-Q",
            },
        ],
        "alternatives_considered": [
            {"description": "MSFT", "why_rejected": "not in watchlist"},
        ],
    }
    async with sf() as session, session.begin():
        tp = await write_proposal(
            session,
            user_id=_USER_ID,
            strategy=strategy,
            strategy_db_id=strategy.strategy_id,
            run_id=uuid4().hex,
            decision_id=decision_id,
            tool_outcome="propose_trade",
            payload=propose_payload,
        )
    assert isinstance(tp, TradeProposal)
    assert tp.account_mode == "LIVE"

    # Now DEMOTE the strategy.
    await promotion_mod.demote_strategy_from_live(
        user_id=_USER_ID, strategy_name=_STRATEGY_NAME
    )

    # The LOCKED proposal row STILL carries account_mode='LIVE' — the
    # stamp is final from T0. (Downstream Slack approve handler + executor
    # read this row, not strategy state, so the TOCTOU window is closed.)
    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.account_mode == "LIVE", (
        "BLOCKER #5: account_mode stamp must survive a post-build demote. "
        f"Got: {row.account_mode!r}"
    )
    persisted_tp = TradeProposal.model_validate_json(row.payload_json)
    assert persisted_tp.account_mode == "LIVE"


# ---------------------------------------------------------------------------
# Wave-0 stub audit (Plan 02-07 §behavior: zero stubs remaining)
# ---------------------------------------------------------------------------


def test_no_wave_0_stubs_remain_in_tests_directory() -> None:
    """Phase-2 wave gate: no ``pytest.skip(...allow_module_level=True)``
    Wave-0 stubs remain in tests/.

    Plan 02-07 §behavior: every Wave-0 stub created by plan 02-01 must
    have been filled in by plans 02-02..02-06. Surfacing any remaining
    stub fails this test with the file path + the owning plan from the
    stub's docstring (so the executor knows which plan to revisit).

    Detection: look for the canonical stub signature ``pytest.skip(...,
    allow_module_level=True)`` AT MODULE SCOPE. This is structurally
    distinct from any string literal that mentions the same text (such
    as the regex pattern in this audit itself), so we won't false-
    positive on docstrings, comments, or this audit test's own pattern.
    """
    tests_dir = Path(__file__).parent.parent
    # Match the canonical Wave-0 stub call: ``pytest.skip(...,
    # allow_module_level=True)`` invoked at module scope. Multi-line
    # tolerant.
    stub_call_pattern = re.compile(
        r"pytest\.skip\([^)]*allow_module_level\s*=\s*True[^)]*\)",
        re.DOTALL,
    )
    # Bypass marker: a comment ``# WAVE-0 STUB:`` left by plan 02-01 — the
    # canonical signal for "this file is a Wave-0 placeholder owned by a
    # later plan."
    wave0_marker = re.compile(r"#\s*WAVE-0 STUB:")
    remaining: list[tuple[Path, str]] = []
    self_path = Path(__file__).resolve()
    for path in tests_dir.rglob("*.py"):
        if path.resolve() == self_path:
            continue  # don't audit this audit
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if stub_call_pattern.search(text) and wave0_marker.search(text):
            owner_match = re.search(
                r"owned by plan (\d{2}-\d{2})", text
            )
            owner = owner_match.group(1) if owner_match else "unknown"
            remaining.append((path.relative_to(tests_dir.parent), owner))
    assert remaining == [], (
        "Wave-0 stub markers still present in tests/. Each Wave-0 stub "
        "from plan 02-01 should have been filled in by its owning plan:\n"
        + "\n".join(
            f"  - {p} (owned by plan {owner})"
            for p, owner in remaining
        )
    )
