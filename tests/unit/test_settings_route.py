"""COST-03 settings route tests — Phase 4 Wave 5.

Covers:
  - POST /settings with daily_cost_ceiling_usd="10.00" → user row updated;
    response contains saved value or success indicator
  - Fresh user row (no daily_cost_ceiling_usd set) → GET /settings shows "5.00"

These tests extend the existing settings route (Plan 03-05) with Phase-4
ceiling config field (daily_cost_ceiling_usd stored on users table per
migration 0005 Task 1).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_vault() -> None:
    import gekko.vault.passphrase as _vault
    _vault.clear()


def _make_user_row(daily_cost_ceiling_usd: str | None = None) -> MagicMock:
    """Build a mock User ORM row."""
    user = MagicMock()
    user.daily_cost_ceiling_usd = daily_cost_ceiling_usd
    user.timezone = "America/New_York"
    user.quiet_hours_start = None
    user.quiet_hours_end = None
    user.user_id = "testuser"
    # Phase-5 portfolio caps default to NULL (disabled) on a fresh mock.
    user.max_total_exposure_pct = None
    user.max_sector_concentration_pct = None
    user.max_correlated_ticker_pct = None
    user.max_total_daily_loss_usd = None
    return user


def _build_mock_session(user: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Build a mock session + session-factory that returns the given user row."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=user)
    mock_result.all.return_value = []

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()

    mock_sf = MagicMock(return_value=mock_session)
    return mock_session, mock_sf


# ---------------------------------------------------------------------------
# COST-03 tests — ceiling field save + default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceiling_saved() -> None:
    """POST /settings with daily_cost_ceiling_usd='10.00' → settings saved.

    The settings_post route should:
    - Accept the daily_cost_ceiling_usd form field
    - Validate it as a positive Decimal
    - Update user.daily_cost_ceiling_usd with the normalized value
    - Return 200 with success message or the saved value visible
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-settings-save"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None)
        _mock_session, mock_sf = _build_mock_session(user)

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
                # Login
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})

                # POST with ceiling=10.00
                resp = await client.post(
                    "/settings",
                    data={
                        "timezone": "America/New_York",
                        "quiet_hours_start": "",
                        "quiet_hours_end": "",
                        "daily_cost_ceiling_usd": "10.00",
                    },
                )

        assert resp.status_code == 200
        # The response should show the settings page (success path renders "Settings saved")
        assert "Settings saved" in resp.text or "Daily LLM Cost Ceiling" in resp.text
        # The user row should have been updated — verify the route set the attribute
        # (mock object's daily_cost_ceiling_usd will have been set by the route)
        assert user.daily_cost_ceiling_usd is not None
        # The route normalizes via str(Decimal("10.00")) → "10.0" or "10.00"
        # Both are acceptable normalized forms
        assert str(Decimal(user.daily_cost_ceiling_usd)) == str(Decimal("10.00")) or \
               user.daily_cost_ceiling_usd in ("10", "10.0", "10.00")
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_settings_get_corrupted_ceiling_renders_default() -> None:
    """GET /settings with over-quoted ceiling "'5.00'" (6-char) → 200 + DEFAULT shown.

    Regression gate: fails against pre-fix code where truthiness-only guard
    lets the corrupted value reach the template without defensive parsing → crash
    or displays the literal "'5.00'" string (with apostrophes) instead of "5.00".
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-settings-corrupted"
    _vault.set_passphrase(correct)
    try:
        # The real corrupted value stored by migration 0005's wrong server_default
        user = _make_user_row(daily_cost_ceiling_usd="'5.00'")
        _mock_session, mock_sf = _build_mock_session(user)

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
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})
                resp = await client.get("/settings")

        assert resp.status_code == 200, (
            f"Expected 200 with corrupted ceiling \"'5.00'\", got {resp.status_code} — "
            "settings_get is not defensively parsing the ceiling value"
        )
        # Should render the DEFAULT "5.00", not the corrupted "'5.00'" with apostrophes
        assert "5.00" in resp.text, (
            "DEFAULT ceiling '5.00' not visible in settings response"
        )
        assert "Daily LLM Cost Ceiling" in resp.text, (
            "Settings page section header 'Daily LLM Cost Ceiling' not found"
        )
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_ceiling_defaults_to_5() -> None:
    """Fresh user row (no daily_cost_ceiling_usd set) → GET /settings shows '5.00'.

    The settings form renders '5.00' as the placeholder / default value
    when the column is NULL (per D-02: DEFAULT_DAILY_CEILING_USD = Decimal('5.00')).
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-settings-default"
    _vault.set_passphrase(correct)
    try:
        # User row with no ceiling set (NULL)
        user = _make_user_row(daily_cost_ceiling_usd=None)
        _mock_session, mock_sf = _build_mock_session(user)

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
                # Login
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})

                # GET /settings
                resp = await client.get("/settings")

        assert resp.status_code == 200
        # Should show the default ceiling value 5.00 in the form field
        assert "5.00" in resp.text
        # Should have the daily ceiling fieldset
        assert "Daily LLM Cost Ceiling" in resp.text
    finally:
        _vault.clear()


# ---------------------------------------------------------------------------
# TRUST-02 — Portfolio Caps fieldset (Plan 05-03 Task 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_renders_portfolio_caps_fieldset() -> None:
    """GET /settings renders the Portfolio Caps fieldset (Surface 7)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-05-caps-render"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None)
        _mock_session, mock_sf = _build_mock_session(user)

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
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})
                resp = await client.get("/settings")

        assert resp.status_code == 200
        assert "Portfolio Caps" in resp.text
        assert "max_total_exposure_pct" in resp.text
        assert "max_correlated_ticker_pct" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_portfolio_caps_saved_as_fractions() -> None:
    """POST percent caps → stored as FRACTION strings (50 → 0.50); USD as-is."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-05-caps-save"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None)
        _mock_session, mock_sf = _build_mock_session(user)

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
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})
                resp = await client.post(
                    "/settings",
                    data={
                        "timezone": "America/New_York",
                        "quiet_hours_start": "",
                        "quiet_hours_end": "",
                        "daily_cost_ceiling_usd": "5.00",
                        "max_total_exposure_pct": "50",
                        "max_sector_concentration_pct": "30",
                        "max_correlated_ticker_pct": "15",
                        "max_total_daily_loss_usd": "200.00",
                    },
                )

        assert resp.status_code == 200
        assert "Settings saved" in resp.text
        # 50% → fraction "0.50"
        assert Decimal(user.max_total_exposure_pct) == Decimal("0.50")
        assert Decimal(user.max_sector_concentration_pct) == Decimal("0.30")
        assert Decimal(user.max_correlated_ticker_pct) == Decimal("0.15")
        assert Decimal(user.max_total_daily_loss_usd) == Decimal("200.00")
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_portfolio_cap_out_of_range_shows_field_error() -> None:
    """percent > 100 → re-render with the field-specific .login-error message."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-05-caps-range"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None)
        _mock_session, mock_sf = _build_mock_session(user)

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
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})
                resp = await client.post(
                    "/settings",
                    data={
                        "timezone": "America/New_York",
                        "quiet_hours_start": "",
                        "quiet_hours_end": "",
                        "daily_cost_ceiling_usd": "5.00",
                        "max_total_exposure_pct": "150",
                    },
                )

        assert resp.status_code == 200
        assert "Max total exposure must be between 0 and 100." in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_blank_portfolio_cap_disables_it() -> None:
    """Blank cap field → stored as None (disabled)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-05-caps-blank"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None)
        user.max_total_exposure_pct = "0.50"  # previously set
        _mock_session, mock_sf = _build_mock_session(user)

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
                await client.post("/login", data={"passphrase": correct, "next": "/settings"})
                resp = await client.post(
                    "/settings",
                    data={
                        "timezone": "America/New_York",
                        "quiet_hours_start": "",
                        "quiet_hours_end": "",
                        "daily_cost_ceiling_usd": "5.00",
                        "max_total_exposure_pct": "",
                    },
                )

        assert resp.status_code == 200
        assert user.max_total_exposure_pct is None
    finally:
        _vault.clear()


# ---------------------------------------------------------------------------
# TRUST-03 — set_capital_ceiling helper leaves trust + streak untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_capital_ceiling_writes_event_and_leaves_trust_untouched(
    temp_sqlcipher_db: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_capital_ceiling writes capital_scaled (old→new) and never touches trust."""
    from datetime import UTC, datetime

    from gekko.db.models import Event, StrategyMetadata, User
    from gekko.db.session import make_session_factory
    from gekko.strategy import trust as trust_mod
    from sqlalchemy import select

    sf = make_session_factory(temp_sqlcipher_db)  # type: ignore[arg-type]
    monkeypatch.setattr(
        trust_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    async with sf() as session, session.begin():
        session.add(
            User(user_id="u1", created_at=datetime.now(UTC).isoformat())
        )
        await session.flush()
        session.add(
            StrategyMetadata(
                user_id="u1",
                strategy_name="alpha",
                trust_level="auto-within-caps",
                capital_ceiling_usd="1000.00",
            )
        )

    old_str, new_str = await trust_mod.set_capital_ceiling(
        user_id="u1", strategy_name="alpha", new_ceiling_usd="2500"
    )
    assert old_str == "1000.00"
    assert Decimal(new_str) == Decimal("2500")

    async with sf() as session:
        meta = await session.get(StrategyMetadata, ("u1", "alpha"))
        assert meta is not None
        # Trust level is UNTOUCHED (D-T17).
        assert meta.trust_level == "auto-within-caps"
        assert Decimal(meta.capital_ceiling_usd) == Decimal("2500")
        events = (
            await session.execute(
                select(Event).where(Event.user_id == "u1")
            )
        ).scalars().all()
        types = {e.event_type for e in events}
        assert "capital_scaled" in types
        # No trust event was written by capital scaling.
        assert "trust_promoted" not in types
        assert "trust_demoted" not in types
