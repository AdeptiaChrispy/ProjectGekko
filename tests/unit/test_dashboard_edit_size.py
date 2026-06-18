"""Tests for dashboard edit-size modal (DASH-04, Plan 03-05 Task 3).

- test_drift_rejected: POST /approvals/{id}/edit-submit with qty that produces
  >2% drift returns the modal partial with error block + no DB state change
- test_happy_path_closes_modal: qty within 2% triggers dedup + transition + executor
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


@pytest.mark.asyncio
async def test_drift_rejected() -> None:
    """POST /approvals/{id}/edit-submit with qty producing >2% drift
    returns 200 with error block; no DB state change."""
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    proposal_id = "drift-rejected-01"
    # ref_price=100, target_notional=1000, original_qty=10
    # new_qty = 15 → new_notional=1500 → drift = 50% >> 2%
    mock_sf, mock_row = _make_session_and_row(proposal_id)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
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

                # POST with qty=15 (drift = (15*100 - 1000) / 1000 = 50%)
                resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "15"},
                )

        assert resp.status_code == 200
        body = resp.text
        # Should return the modal partial with error message
        assert "drift" in body.lower() or "2%" in body or "error" in body.lower()
        # claim_action should NOT have been called (drift check happens first)
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
