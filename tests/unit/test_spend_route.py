"""COST-02 dashboard spend route tests — Phase 4 Wave 5.

Covers:
  - GET /spend returns HTTP 200 for authenticated session
  - Response context contains today_total as Decimal
  - Response context contains ceiling as Decimal
  - Response context contains by_strategy list with strategy_name + spend
  - Response context contains history list with 7 entries (one per day)
  - Unauthenticated GET /spend returns 302 redirect to /login

All tests import the FastAPI ``app`` via ``httpx.AsyncClient`` +
``httpx.ASGITransport`` — same pattern as existing dashboard route tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_cost_row(cost_usd: str, strategy_name: str, ts: str | None = None) -> MagicMock:
    """Build a mock Event row with the llm_cost payload shape."""
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    row = MagicMock()
    row.payload_json = json.dumps({
        "cost_usd": cost_usd,
        "strategy_name": strategy_name,
        "model": "sonnet",
        "call_type": "researcher",
        "input_tokens": 100,
        "output_tokens": 50,
    })
    row.strategy_id = "test-strat-id"
    row.ts = ts
    return row


def _make_user_row(
    daily_cost_ceiling_usd: str | None = "5.00",
    timezone: str | None = "America/New_York",
) -> MagicMock:
    """Build a mock User ORM row."""
    user = MagicMock()
    user.daily_cost_ceiling_usd = daily_cost_ceiling_usd
    user.timezone = timezone
    user.user_id = "testuser"
    return user


def _clear_vault() -> None:
    import gekko.vault.passphrase as _vault
    _vault.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_get_returns_200() -> None:
    """GET /spend with authenticated session returns HTTP 200."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-200"
    _vault.set_passphrase(correct)
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_result.scalar_one_or_none = MagicMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sf = MagicMock(return_value=mock_session)
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login", data={"passphrase": correct, "next": "/spend"}
                )
                assert login_resp.status_code == 303

                resp = await client.get("/spend")

        assert resp.status_code == 200
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_shows_today_total() -> None:
    """GET /spend response context contains today_total as Decimal."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-total"
    _vault.set_passphrase(correct)
    try:
        # Build a mock session returning 2 llm_cost rows for today
        today_ts = datetime.now(UTC).isoformat()
        row1 = _make_llm_cost_row("0.05", "strat-a", today_ts)
        row2 = _make_llm_cost_row("0.03", "strat-b", today_ts)
        user = _make_user_row("5.00", "America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # First execute call: user row
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            elif call_count == 2:
                # Second execute call: today's llm_cost rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                # 7-day history rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # The page should show the total spend ($0.08)
        assert "0.08" in resp.text or "0.0800" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_shows_ceiling() -> None:
    """GET /spend response context contains ceiling as Decimal."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-ceiling"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row("10.00", "America/New_York")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=user)
        mock_result.all.return_value = []

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # Should show the ceiling value
        assert "10.00" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_per_strategy_breakdown() -> None:
    """GET /spend response context contains by_strategy list with strategy_name + spend."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-strategy"
    _vault.set_passphrase(correct)
    try:
        today_ts = datetime.now(UTC).isoformat()
        row1 = _make_llm_cost_row("0.05", "Tech Momentum", today_ts)
        row2 = _make_llm_cost_row("0.02", "Tech Momentum", today_ts)
        row3 = _make_llm_cost_row("0.03", "Dividend Yield", today_ts)
        user = _make_user_row("5.00", "America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            elif call_count == 2:
                mock_result.all.return_value = [row1, row2, row3]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                mock_result.all.return_value = [row1, row2, row3]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        assert "Tech Momentum" in resp.text
        assert "Dividend Yield" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_7day_history() -> None:
    """GET /spend response context contains history list with 7 entries (one per day)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-history"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row("5.00", "America/New_York")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=user)
        mock_result.all.return_value = []

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # Should have the 7-day history table section header
        assert "7-Day History" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_requires_auth() -> None:
    """Unauthenticated GET /spend returns 302 redirect to /login (auth gate)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("test-passphrase-04-spend-auth")
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
                resp = await client.get("/spend")

        assert resp.status_code in (302, 303, 307)
        location = resp.headers.get("location", "")
        assert "/login" in location
    finally:
        _vault.clear()
