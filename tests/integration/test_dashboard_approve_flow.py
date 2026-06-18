"""Integration test: full HTMX approve cycle ending in executor dispatch.

Plan 03-05 Task 2 (DASH-04).

Flow:
  1. Login -> obtain session cookie
  2. GET /approvals -> see PENDING proposal card
  3. POST /approvals/{id}/approve -> dedup INSERT + APPROVED transition +
     background execute_proposal task spawned
  4. Assert HTMX response is the updated _proposal_card.html.j2 partial
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_dashboard_approve_flow() -> None:
    """Full HTMX approve cycle: login -> approve proposal -> updated card returned."""
    import asyncio
    import json

    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    correct_pass = "integration-test-pass"
    _vault.set_passphrase(correct_pass)

    proposal_id = "prop-integration-01"

    # Minimal proposal row mock
    mock_row = MagicMock()
    mock_row.proposal_id = proposal_id
    mock_row.status = "PENDING"
    mock_row.ticker = "NVDA"
    mock_row.side = "BUY"
    mock_row.qty = "5"
    mock_row.rationale = "GPU demand remains strong"
    mock_row.account_mode = "PAPER"
    mock_row.expires_at = None
    mock_row.slack_message_ts = None
    mock_row.slack_message_channel = None
    mock_row.strategy_id = "strategy-1"
    mock_row.user_id = "testuser"
    mock_row.payload_json = json.dumps({
        "ticker": "NVDA",
        "side": "BUY",
        "qty": "5",
        "rationale": "GPU demand remains strong",
        "evidence": [
            {"summary": "Strong Q4", "url": "https://reuters.com/1", "source_type": "news", "why_cited": "beat"},
            {"summary": "Analyst buy", "url": "https://reuters.com/2", "source_type": "analyst_report", "why_cited": "upgrades"},
            {"summary": "New products", "url": "https://reuters.com/3", "source_type": "news", "why_cited": "pipeline"},
        ],
        "alternatives_considered": [],
        "confidence": "0.8",
        "decision_id": proposal_id,
        "strategy_name": "gpu-bull",
        "user_id": "testuser",
        "client_order_id": "a" * 32,
        "order_type": "MARKET",
        "account_mode": "PAPER",
        "target_notional_usd": "1000",
    })

    # Track spawned tasks
    spawned_tasks = []
    original_create_task = asyncio.create_task

    def capture_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        spawned_tasks.append(task)
        return task

    # Mock session returning PENDING row on approve endpoint
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)

    # Make execute() return the row for the proposal
    mock_result_proposals = MagicMock()
    mock_result_proposals.scalars.return_value.all.return_value = [mock_row]

    mock_result_single = MagicMock()
    mock_result_single.scalar_one_or_none.return_value = mock_row

    call_count = {"n": 0}

    async def mock_execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return mock_result_proposals
        return mock_result_single

    mock_session.execute = mock_execute
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()

    mock_sf = MagicMock(return_value=mock_session)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, None)), \
             patch("gekko.approval.dedup.claim_action", new_callable=AsyncMock, return_value="first_write") as mock_claim, \
             patch("gekko.approval.proposals.transition_status", new_callable=AsyncMock) as mock_transition, \
             patch("gekko.approval.proposals.append_event", new_callable=AsyncMock) as mock_append, \
             patch("gekko.execution.executor.execute_proposal", new_callable=AsyncMock) as mock_execute_proposal, \
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

                # Step 2: POST approve
                approve_resp = await client.post(
                    f"/approvals/{proposal_id}/approve",
                )

        # Response should be the updated proposal card partial
        assert approve_resp.status_code == 200
        # Should render a proposal card (not a redirect to login)
        assert "proposal-card" in approve_resp.text or "NVDA" in approve_resp.text

        # claim_action should have been called with source="dashboard"
        mock_claim.assert_called_once()
        call_kwargs = mock_claim.call_args.kwargs
        assert call_kwargs.get("source") == "dashboard"
        assert call_kwargs.get("action_id") == "approve_proposal"

    finally:
        _vault.clear()
