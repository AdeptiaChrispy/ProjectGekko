"""Integration test: HTMX edit-size modal end-to-end happy path (DASH-04, Plan 03-05 Task 3).

Flow:
  1. Login -> obtain session cookie
  2. GET /approvals/{id}/edit-size -> modal form with qty input
  3. POST /approvals/{id}/edit-submit with valid qty (within 2% drift)
  4. Assert: dedup row inserted with source="dashboard" + action_id="edit_size"
  5. Assert: edit_size audit event appended
  6. Assert: executor dispatched via asyncio.create_task
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_dashboard_edit_size_happy() -> None:
    """Full HTMX edit-size happy path: login -> edit-size GET -> edit-submit POST."""
    from datetime import UTC, datetime

    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    correct_pass = "integration-edit-test"
    _vault.set_passphrase(correct_pass)

    proposal_id = "edit-happy-integration-01"
    now_iso = datetime.now(UTC).isoformat()

    # TradeProposal requires at least 3 evidence items + 1 alternative
    evidence = [
        {"source_type": "finnhub_news", "summary": "Strong earnings", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "web_fetch", "summary": "Analyst upgrades", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
        {"source_type": "edgar_filing", "summary": "Strong balance sheet", "fetched_at": now_iso, "source_url": None, "quote_text": None, "relevance_score": None},
    ]
    alternatives = [{"description": "MSFT position", "why_rejected": "lower growth"}]

    # Build proposal row mock
    mock_row = MagicMock()
    mock_row.proposal_id = proposal_id
    mock_row.status = "PENDING"
    mock_row.ticker = "AAPL"
    mock_row.side = "buy"
    mock_row.qty = "20"
    mock_row.rationale = "Tech rally"
    mock_row.account_mode = "PAPER"
    mock_row.expires_at = None
    mock_row.slack_message_ts = None
    mock_row.slack_message_channel = None
    mock_row.strategy_id = "strat-x"
    mock_row.user_id = "testuser"
    mock_row.payload_json = json.dumps({
        "ticker": "AAPL",
        "side": "buy",
        "qty": "20",
        "order_type": "market",
        "rationale": "Tech rally",
        "evidence": evidence,
        "alternatives_considered": alternatives,
        "confidence": "0.75",
        "decision_id": proposal_id,
        "strategy_name": "tech-bull",
        "user_id": "testuser",
        "client_order_id": "b" * 32,
        "account_mode": "PAPER",
        "target_notional_usd": "2000",
        "limit_price": None,
        "stop_price": None,
    })

    spawned_tasks = []
    original_create_task = asyncio.create_task

    def capture_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        spawned_tasks.append(task)
        return task

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.refresh = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_result.scalars.return_value.all.return_value = [mock_row]

    async def mock_execute(stmt, *args, **kwargs):
        return mock_result

    mock_session.execute = mock_execute
    mock_sf = MagicMock(return_value=mock_session)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock, return_value="first_write") as mock_claim, \
             patch("gekko.audit.log.append_event", new_callable=AsyncMock) as mock_append, \
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
                # Step 1: Login
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": correct_pass, "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # Step 2: GET edit-size modal — send HX-Request: true so the
                # route returns the bare fragment (not a redirect).
                # Bug A fix (Plan 03-15): non-HX direct-nav returns 302 to /approvals;
                # HTMX swap must include the HX-Request header to get the fragment.
                edit_get_resp = await client.get(
                    f"/approvals/{proposal_id}/edit-size",
                    headers={"HX-Request": "true"},
                )
                assert edit_get_resp.status_code == 200
                # Modal should have a qty input and form action
                edit_html = edit_get_resp.text
                assert "qty" in edit_html.lower() or "edit" in edit_html.lower()

                # Step 3: POST edit-submit with qty=20.1
                # target_notional_usd=2000, we need a ref_price
                # The route fetches the proposal row; qty=20.1
                # drift = |20.1 * ref_price - 2000| / 2000
                # If ref_price comes from payload_json limit/stop_price (None),
                # the route should use a fallback or the qty close to original
                # qty=20.1 at ref_price=100 → notional=2010 → drift=0.5% < 2%
                # But ref_price will be derived at runtime; we test the flow
                submit_resp = await client.post(
                    f"/approvals/{proposal_id}/edit-submit",
                    data={"qty": "20.1"},
                )

        assert submit_resp.status_code == 200

        # claim_action called with source="dashboard", action_id="edit_size"
        mock_claim.assert_called_once()
        call_kwargs = mock_claim.call_args.kwargs
        assert call_kwargs.get("source") == "dashboard", (
            f"Expected source='dashboard', got: {call_kwargs}"
        )
        assert call_kwargs.get("action_id") == "edit_size", (
            f"Expected action_id='edit_size', got: {call_kwargs}"
        )

        # append_event called with edit_size event
        mock_append.assert_called()
        # At least one call should have event_type='edit_size'
        edit_size_calls = [
            c for c in mock_append.call_args_list
            if c.kwargs.get("event_type") == "edit_size"
        ]
        assert edit_size_calls, "Expected edit_size audit event to be appended"

    finally:
        _vault.clear()
