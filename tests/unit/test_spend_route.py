"""COST-02 dashboard spend route stubs — Phase 4 Wave 0.

Covers:
  - GET /spend returns HTTP 200 for authenticated session
  - Response context contains today_total as Decimal
  - Response context contains ceiling as Decimal
  - Response context contains by_strategy list with strategy_name + spend
  - Response context contains history list with 7 entries (one per day)
  - Unauthenticated GET /spend returns 302 redirect to /login

All tests import the FastAPI ``app`` via ``httpx.AsyncClient`` +
``httpx.ASGITransport`` — same pattern as existing dashboard route tests.
The ``GET /spend`` route does NOT exist yet, so most tests will fail at
collection or runtime with a 404/ImportError. This is the expected RED state.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app_with_mock_db(spend_data: dict | None = None):
    """Build a create_app() instance with mocked settings + DB session.

    ``spend_data`` controls what the mocked DB returns for the spend route.
    If None, defaults to: today_total=Decimal('0'), ceiling=Decimal('5.00'),
    by_strategy=[], history=[].
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("test-passphrase-04-spend")

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)

    # Default spend data
    if spend_data is None:
        spend_data = {
            "today_total": Decimal("0"),
            "ceiling": Decimal("5.00"),
            "by_strategy": [],
            "history": [{"date": f"2026-06-{17 + i:02d}", "spend": Decimal("0")} for i in range(7)],
        }

    mock_result = MagicMock()
    mock_result.all.return_value = []
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

    return app, spend_data


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
    # The template must receive today_total as a Decimal (not float).
    # Implementation: the route fetches llm_cost events for today and sums
    # payload['cost_usd'] values using Decimal arithmetic.
    raise NotImplementedError(
        "stub — implement after GET /spend route ships in Wave 3"
    )


@pytest.mark.asyncio
async def test_spend_get_shows_ceiling() -> None:
    """GET /spend response context contains ceiling as Decimal."""
    # The template must receive ceiling = user.daily_cost_ceiling_usd parsed
    # as Decimal (or DEFAULT_DAILY_CEILING_USD if the column is NULL).
    raise NotImplementedError(
        "stub — implement after GET /spend route ships in Wave 3"
    )


@pytest.mark.asyncio
async def test_spend_get_per_strategy_breakdown() -> None:
    """GET /spend response context contains by_strategy list with strategy_name + spend."""
    # The by_strategy list groups today's llm_cost events by strategy_name,
    # summing cost_usd per strategy (Decimal). Empty list is acceptable when no
    # costs have been logged today.
    raise NotImplementedError(
        "stub — implement after GET /spend route ships in Wave 3"
    )


@pytest.mark.asyncio
async def test_spend_get_7day_history() -> None:
    """GET /spend response context contains history list with 7 entries (one per day)."""
    # The 7-day history is a list of dicts [{date, spend}] covering the past 7
    # calendar days in the user's configured timezone (D-11).
    raise NotImplementedError(
        "stub — implement after GET /spend route ships in Wave 3"
    )


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
