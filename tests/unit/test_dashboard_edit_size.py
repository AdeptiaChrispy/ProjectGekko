"""Tests for dashboard edit-size modal (DASH-04, Plan 03-05 Task 3; cap redesign Plan 03-11).

- test_edit_above_hard_cap_rejected: POST /approvals/{id}/edit-submit with qty whose
  notional exceeds the strategy's OrderGuard hard cap (max_position_pct * equity)
  returns the modal partial with a plain-language error block + no DB state change.
  This replaced the old 2%-drift gate (Plan 03-11 / D-54): operator edits are validated
  against absolute risk bounds, not consistency with the agent's original target_notional.
- test_happy_path_closes_modal: valid qty triggers dedup + transition + executor
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_session_and_row(proposal_id: str, ref_price_str: str = "100.00"):
    """Build mock session factory + proposal row for edit-size tests."""
    from datetime import UTC, datetime

    import gekko.vault.passphrase as _vault
    _vault.set_passphrase("test-pass-edit")

    now_iso = datetime.now(UTC).isoformat()
    # TradeProposal requires at least 3 evidence items + 1 alternative
    evidence = [
        {"source_type": "finnhub_news", "summary": "Strong earnings", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "web_fetch", "summary": "Analyst upgrades", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "edgar_filing", "summary": "Strong balance sheet", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
    ]
    alternatives = [{"description": "RIVN position", "why_rejected": "lower margin"}]

    mock_row = MagicMock()
    mock_row.proposal_id = proposal_id
    mock_row.status = "PENDING"
    mock_row.ticker = "TSLA"
    mock_row.side = "buy"
    mock_row.qty = "10"
    mock_row.rationale = "EV thesis"
    mock_row.account_mode = "PAPER"
    mock_row.expires_at = None
    mock_row.slack_message_ts = None
    mock_row.slack_message_channel = None
    mock_row.strategy_id = "strat-1"
    mock_row.user_id = "testuser"
    mock_row.payload_json = json.dumps({
        "ticker": "TSLA",
        "side": "buy",
        "qty": "10",
        "order_type": "market",
        "rationale": "EV thesis",
        "evidence": evidence,
        "alternatives_considered": alternatives,
        "confidence": "0.8",
        "decision_id": proposal_id,
        "strategy_name": "ev-bull",
        "user_id": "testuser",
        "client_order_id": "a" * 32,
        "account_mode": "PAPER",
        "target_notional_usd": "1000",
        "limit_price": None,
        "stop_price": None,
    })

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.refresh = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_result.scalars.return_value.all.return_value = []

    async def mock_execute(stmt, *args, **kwargs):
        return mock_result

    mock_session.execute = mock_execute
    mock_sf = MagicMock(return_value=mock_session)
    return mock_sf, mock_row


def _make_strategy_row(max_position_pct: str = "0.20"):
    """Build a mock strategy row whose payload_json is a valid Strategy.

    Used by the cap-rejection test so edit_size_submit can resolve
    strategy.hard_caps.max_position_pct for _check_edit_size_caps.
    """
    from decimal import Decimal

    from gekko.schemas.strategy import HardCaps, Strategy

    strat = Strategy.model_validate(
        {
            "strategy_id": "strat-1",
            "user_id": "testuser",
            "name": "EV Bull",
            "version": 1,
            "thesis": "EV thesis for cap-rejection test.",
            "watchlist": ["TSLA"],
            "hard_caps": HardCaps(
                max_position_pct=Decimal(max_position_pct),
                max_daily_loss_usd=Decimal("500"),
                max_trades_per_day=5,
                max_sector_exposure_pct=Decimal("0.50"),
            ),
            "created_at": "2026-06-19T00:00:00+00:00",
        }
    )
    strategy_row = MagicMock()
    strategy_row.payload_json = strat.model_dump_json()
    return strategy_row


@pytest.mark.asyncio
async def test_live_proposal_strategy_load_failure_rejected() -> None:
    """CR-01 regression: when strategy cannot be loaded for a LIVE proposal,
    the edit must be REJECTED (fail-closed), not silently allowed through.

    Uses a LIVE proposal row whose payload_json has account_mode='LIVE'.
    The mock session returns None for the strategy row (simulating a missing
    or deleted strategy). The dashboard must return the modal with a safety
    error message and must NOT call claim_action.
    """
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "live-no-strategy-01"
    _vault.set_passphrase("test-pass-live-fail")

    from datetime import UTC, datetime
    now_iso = datetime.now(UTC).isoformat()
    evidence = [
        {"source_type": "finnhub_news", "summary": "Strong earnings", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "web_fetch", "summary": "Analyst upgrades", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "edgar_filing", "summary": "Strong balance sheet", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
    ]
    alternatives = [{"description": "RIVN position", "why_rejected": "lower margin"}]

    # Build a LIVE proposal row (account_mode = "LIVE")
    mock_row = MagicMock()
    mock_row.proposal_id = proposal_id
    mock_row.status = "PENDING"
    mock_row.ticker = "TSLA"
    mock_row.side = "buy"
    mock_row.qty = "10"
    mock_row.rationale = "EV thesis"
    mock_row.account_mode = "LIVE"
    mock_row.expires_at = None
    mock_row.slack_message_ts = None
    mock_row.slack_message_channel = None
    mock_row.strategy_id = "strat-live"
    mock_row.user_id = "testuser"
    mock_row.payload_json = json.dumps({
        "ticker": "TSLA",
        "side": "buy",
        "qty": "10",
        "order_type": "market",
        "rationale": "EV thesis",
        "evidence": evidence,
        "alternatives_considered": alternatives,
        "confidence": "0.8",
        "decision_id": proposal_id,
        "strategy_name": "live-strat",
        "user_id": "testuser",
        "client_order_id": "a" * 32,
        "account_mode": "LIVE",
        "target_notional_usd": "1000",
        "limit_price": None,
        "stop_price": None,
    })

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.refresh = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()

    # call 1 = proposal load (returns mock_row), call 2 = strategy load (returns None)
    call_count = {"n": 0}
    proposal_result = MagicMock()
    proposal_result.scalar_one_or_none.return_value = mock_row
    no_strategy_result = MagicMock()
    no_strategy_result.scalar_one_or_none.return_value = None  # strategy missing

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        return proposal_result if call_count["n"] == 1 else no_strategy_result

    mock_session.execute = mock_execute
    mock_sf = MagicMock(return_value=mock_session)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.brokers.alpaca.AlpacaBroker", side_effect=Exception("no broker")), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock) as mock_claim:

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-live-fail", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "10"},
                )

        assert resp.status_code == 200
        body = resp.text
        # Must contain the safety-rejection message (fail-closed on LIVE)
        assert "risk caps" in body.lower() or "blocked" in body.lower() or "safety" in body.lower()
        # claim_action must NOT be called — edit is rejected before dedup
        mock_claim.assert_not_called()
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_edit_size_get_context_keys() -> None:
    """GET /approvals/{id}/edit-size response contains input[type="range"] with
    correct HTML attributes: name="qty", min="1", step="1".

    The route must pass max_shares (int), account_equity_display (str),
    equity_fetch_failed (bool), and max_position_pct (str) to the template.
    """
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "edit-get-ctx-01"

    mock_sf, mock_row = _make_session_and_row(proposal_id)
    # _make_session_and_row sets passphrase to "test-pass-edit"; override here
    _vault.set_passphrase("test-pass-edit")
    strategy_row = _make_strategy_row("0.20")

    call_count = {"n": 0}
    proposal_result = MagicMock()
    proposal_result.scalar_one_or_none.return_value = mock_row
    strategy_result = MagicMock()
    strategy_result.scalar_one_or_none.return_value = strategy_row

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        # GET handler opens 2 sessions: first = proposal, second = strategy
        return proposal_result if call_count["n"] == 1 else strategy_result

    mock_session = mock_sf.return_value
    mock_session.execute = mock_execute

    broker_instance = MagicMock()
    broker_instance.get_account = AsyncMock(return_value={"equity": "10000"})

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.brokers.alpaca.AlpacaBroker", return_value=broker_instance):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                resp = await client.get(f"/approvals/{proposal_id}/edit-size")

        assert resp.status_code == 200
        body = resp.text
        # Must render a range slider (not number input)
        assert 'type="range"' in body
        assert 'name="qty"' in body
        assert 'min="1"' in body
        assert 'step="1"' in body
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_edit_size_get_equity_fail_open() -> None:
    """GET /approvals/{id}/edit-size returns 200 with type="range" even when
    the broker get_account raises (equity-fetch-failure path).

    The caution note text "Cap couldn't be confirmed" must appear in the page.
    """
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "edit-get-fail-01"

    mock_sf, mock_row = _make_session_and_row(proposal_id)
    # _make_session_and_row sets passphrase to "test-pass-edit"; use same value
    _vault.set_passphrase("test-pass-edit")
    strategy_row = _make_strategy_row("0.20")

    call_count = {"n": 0}
    proposal_result = MagicMock()
    proposal_result.scalar_one_or_none.return_value = mock_row
    strategy_result = MagicMock()
    strategy_result.scalar_one_or_none.return_value = strategy_row

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        return proposal_result if call_count["n"] == 1 else strategy_result

    mock_session = mock_sf.return_value
    mock_session.execute = mock_execute

    # Broker raises — simulates equity-fetch failure
    broker_instance = MagicMock()
    broker_instance.get_account = AsyncMock(side_effect=Exception("broker unreachable"))

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.brokers.alpaca.AlpacaBroker", return_value=broker_instance):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                resp = await client.get(f"/approvals/{proposal_id}/edit-size")

        assert resp.status_code == 200
        body = resp.text
        # Slider must still render (fail-open)
        assert 'type="range"' in body
        # Equity-failure caution note must be present
        assert "Cap couldn't be confirmed" in body
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_edit_size_get_uses_payload_not_orm_ticker() -> None:
    """Regression: edit_size_get must read ticker/side/qty from payload_json,
    NOT from non-existent Proposal ORM columns.

    Live UAT 2026-06-22 hit `AttributeError: 'Proposal' object has no attribute
    'ticker'` at routes.py because the handler referenced `row.ticker`. The other
    GET-route tests used a plain MagicMock row (which auto-creates `.ticker`) and
    masked it. Here the proposal row is spec'd to the real Proposal model, so any
    access to a non-column attribute raises AttributeError exactly like prod.
    """
    import json as _json
    from datetime import UTC, datetime

    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app
    from gekko.db.models import Proposal as ProposalRow

    proposal_id = "edit-get-noticker-01"
    _vault.set_passphrase("test-pass-edit")

    now_iso = datetime.now(UTC).isoformat()
    evidence = [
        {"source_type": "finnhub_news", "summary": "x", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "web_fetch", "summary": "y", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "edgar_filing", "summary": "z", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
    ]
    payload = {
        "ticker": "TSLA", "side": "buy", "qty": "10", "order_type": "market",
        "rationale": "EV thesis", "evidence": evidence,
        "alternatives_considered": [{"description": "RIVN", "why_rejected": "lower margin"}],
        "confidence": "0.8", "decision_id": proposal_id, "strategy_name": "ev-bull",
        "user_id": "testuser", "client_order_id": "a" * 32, "account_mode": "PAPER",
        "target_notional_usd": "1000", "limit_price": None, "stop_price": None,
    }

    # spec=ProposalRow → only real columns exist; row.ticker raises AttributeError.
    mock_row = MagicMock(spec=ProposalRow)
    mock_row.proposal_id = proposal_id
    mock_row.user_id = "testuser"
    mock_row.strategy_id = "strat-1"
    mock_row.status = "PENDING"
    mock_row.account_mode = "PAPER"
    mock_row.payload_json = _json.dumps(payload)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    strategy_row = _make_strategy_row("0.20")
    call_count = {"n": 0}
    proposal_result = MagicMock()
    proposal_result.scalar_one_or_none.return_value = mock_row
    strategy_result = MagicMock()
    strategy_result.scalar_one_or_none.return_value = strategy_row

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        return proposal_result if call_count["n"] == 1 else strategy_result

    mock_session.execute = mock_execute
    mock_sf = MagicMock(return_value=mock_session)

    broker_instance = MagicMock()
    broker_instance.get_account = AsyncMock(return_value={"equity": "10000"})

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.brokers.alpaca.AlpacaBroker", return_value=broker_instance):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                resp = await client.get(f"/approvals/{proposal_id}/edit-size")

        assert resp.status_code == 200
        body = resp.text
        assert 'type="range"' in body
        assert "TSLA" in body  # ticker came from payload_json, not row.ticker
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_edit_above_hard_cap_rejected() -> None:
    """POST /approvals/{id}/edit-submit with qty whose notional exceeds the
    strategy's hard cap returns 200 with a plain-language error block; no DB
    state change. Replaces the old 2%-drift gate (Plan 03-11 / D-54)."""
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "cap-rejected-01"
    # ref_price = target_notional/original_qty = 1000/10 = $100
    # new_qty = 15 → new_notional = $1,500
    # cap = max_position_pct(0.20) * equity($5,000) = $1,000 → 1500 > 1000 → REJECTED
    mock_sf, mock_row = _make_session_and_row(proposal_id)
    strategy_row = _make_strategy_row("0.20")

    # execute() call 1 = proposal load, call 2 = strategy load (cap fails before any further calls)
    call_count = {"n": 0}
    proposal_result = MagicMock()
    proposal_result.scalar_one_or_none.return_value = mock_row
    proposal_result.scalars.return_value.all.return_value = []
    strategy_result = MagicMock()
    strategy_result.scalar_one_or_none.return_value = strategy_row

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        return proposal_result if call_count["n"] == 1 else strategy_result

    mock_session = mock_sf.return_value
    mock_session.execute = mock_execute

    # Broker equity fetch → $5,000 (so the cap is a finite $1,000, not fail-open)
    broker_instance = MagicMock()
    broker_instance.get_account = AsyncMock(return_value={"equity": "5000"})

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.brokers.alpaca.AlpacaBroker", return_value=broker_instance), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock) as mock_claim, \
             patch("gekko.approval.proposals.append_event", new_callable=AsyncMock):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                # Login first
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # POST with qty=15 → $1,500 notional > $1,000 cap
                resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "15"},
                )

        assert resp.status_code == 200
        body = resp.text
        # Should return the modal partial with the plain-language cap error
        assert "max" in body.lower() or "error" in body.lower()
        # claim_action must NOT be called — cap check rejects before dedup
        mock_claim.assert_not_called()
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_happy_path_closes_modal() -> None:
    """POST /approvals/{id}/edit-submit with valid qty (within 2%) triggers
    dedup INSERT, edit_size event, qty update, APPROVED transition, executor."""
    import asyncio
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "edit-happy-01"
    mock_sf, mock_row = _make_session_and_row(proposal_id)

    spawned_tasks = []
    original_create_task = asyncio.create_task

    def capture_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        spawned_tasks.append(task)
        return task

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock, return_value="first_write") as mock_claim, \
             patch("gekko.audit.log.append_event", new_callable=AsyncMock), \
             patch("gekko.approval.proposals.transition_status", new_callable=AsyncMock), \
             patch("gekko.execution.executor.execute_proposal", new_callable=AsyncMock), \
             patch("asyncio.create_task", side_effect=capture_create_task):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                # Login
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # GET edit-size modal first
                edit_resp = await client.get(
                    f"/approvals/{proposal_id}/edit-size",
                )
                assert edit_resp.status_code == 200
                assert "edit" in edit_resp.text.lower() or "qty" in edit_resp.text.lower()

                # POST with qty=10.1 → drift=(10.1*100-1000)/1000=1% < 2% → pass
                submit_resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "10.1"},
                )

        # Success path returns empty or the updated card
        assert submit_resp.status_code == 200
        # claim_action should have been called with source="dashboard"
        mock_claim.assert_called_once()
        call_kwargs = mock_claim.call_args.kwargs
        assert call_kwargs.get("source") == "dashboard"
        assert call_kwargs.get("action_id") == "edit_size"
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_edit_updates_target_notional_to_match_new_qty() -> None:
    """Regression: a deliberate resize must rewrite target_notional_usd to
    new_qty * ref_price, else OrderGuard's check_qty_price_sanity (D-27, 2%
    drift of qty*ref_price vs declared target) rejects every real resize.

    Live UAT 2026-06-22: resizing NVDA 2→5 shares passed the edit-size cap check
    but was then [REJECTED BY ORDERGUARD] qty_price_drift because target_notional_usd
    stayed at the agent's original value. The hard cap (max_position_pct*equity)
    remains the real bound; this only keeps qty<->declared-notional consistent.
    """
    import json as _json
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    # _make_session_and_row: qty="10", target_notional_usd="1000" → ref_price=100.
    proposal_id = "edit-target-notional-01"
    mock_sf, mock_row = _make_session_and_row(proposal_id)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock, return_value="first_write"), \
             patch("gekko.audit.log.append_event", new_callable=AsyncMock), \
             patch("gekko.approval.proposals.transition_status", new_callable=AsyncMock), \
             patch("gekko.execution.executor.execute_proposal", new_callable=AsyncMock), \
             patch("asyncio.create_task"):

            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": "test-pass-edit", "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # Resize 10 → 25 shares: new_notional = 25 * 100 = 2500.
                # Old code left target_notional_usd at "1000" → OrderGuard reject.
                submit_resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "25"},
                )

        assert submit_resp.status_code == 200
        # The re-serialized payload must carry the updated declared notional.
        written = _json.loads(mock_row.payload_json)
        assert Decimal(str(written["qty"])) == Decimal("25")
        assert Decimal(str(written["target_notional_usd"])) == Decimal("2500")
    finally:
        _vault.clear()
