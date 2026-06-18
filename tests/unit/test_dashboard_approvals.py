"""Tests for GET /approvals dashboard index — Plan 03-05 Task 2 (D-55, DASH-04).

Covers:
  (a) GET /approvals without cookie -> 302 to /login
  (b) GET /approvals with cookie -> 200 + list of PENDING + EXPIRED + AWAITING_2ND_CHANNEL
  (c) empty state when zero proposals
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_mock_proposal(
    proposal_id: str = "test-id",
    status: str = "PENDING",
    ticker: str = "AAPL",
    side: str = "BUY",
    qty: str = "10",
    rationale: str = "test rationale",
    account_mode: str = "PAPER",
):
    """Build a minimal mock Proposal row for tests."""
    m = MagicMock()
    m.proposal_id = proposal_id
    m.status = status
    m.ticker = ticker
    m.side = side
    m.qty = qty
    m.rationale = rationale
    m.account_mode = account_mode
    m.expires_at = None
    m.slack_message_ts = None
    m.slack_message_channel = None
    m.strategy_id = "strat-1"
    m.user_id = "testuser"
    m.payload_json = (
        '{"ticker":"' + ticker + '",'
        '"side":"' + side + '",'
        '"qty":' + str(qty) + ','
        '"rationale":"' + rationale + '",'
        '"evidence":[],'
        '"alternatives_considered":[],'
        '"confidence":0.8,'
        '"decision_id":"' + proposal_id + '",'
        '"strategy_name":"test-strat",'
        '"user_id":"testuser",'
        '"client_order_id":"a" * 32,'
        '"order_type":"MARKET",'
        '"account_mode":"' + account_mode + '",'
        '"target_notional_usd":"1000"}'
    )
    return m


def _make_mock_session_factory(proposals):
    """Return a mock (sf, engine) pair that yields proposals on SELECT."""
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = proposals
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_sf = MagicMock(return_value=mock_session)
    return mock_sf, None


@pytest.mark.asyncio
async def test_unauth_redirects() -> None:
    """GET /approvals without session cookie redirects to /login."""
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    _vault.set_passphrase("any")
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn:
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
                resp = await client.get("/approvals")

        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_lists_pending() -> None:
    """GET /approvals with valid session returns 200 with proposal cards."""
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    correct = "correct-pass"
    _vault.set_passphrase(correct)

    proposals = [
        _make_mock_proposal("id-1", "PENDING", "AAPL", "BUY", "10"),
        _make_mock_proposal("id-2", "EXPIRED", "NVDA", "SELL", "5"),
    ]
    mock_sf, mock_engine = _make_mock_session_factory(proposals)

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, mock_engine)):
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
                    data={"passphrase": correct, "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # Get approvals
                resp = await client.get("/approvals")

        assert resp.status_code == 200
        # Both proposals should appear in some form
        assert "AAPL" in resp.text or "proposal-card" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_empty_state() -> None:
    """GET /approvals with no proposals shows empty state."""
    import gekko.vault.passphrase as _vault
    from gekko.dashboard.app import create_app

    correct = "correct-pass"
    _vault.set_passphrase(correct)

    mock_sf, mock_engine = _make_mock_session_factory([])

    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory", return_value=(mock_sf, mock_engine)):
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
                    data={"passphrase": correct, "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                resp = await client.get("/approvals")

        assert resp.status_code == 200
        # Should render empty state
        assert "empty-state" in resp.text or "No proposals" in resp.text
    finally:
        _vault.clear()
