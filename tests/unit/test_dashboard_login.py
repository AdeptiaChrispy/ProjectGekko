"""Tests for dashboard login auth — Plan 03-05 Task 1 (D-57 / D-58).

Covers:
  (a) GET /login renders form 200
  (b) POST /login wrong passphrase re-renders with .login-error + no Set-Cookie
  (c) POST /login correct passphrase mints Set-Cookie + 303 redirect to next_url
  (d) GET /approvals without cookie -> 302 redirect to /login?next=/approvals
  (e) GET /approvals with valid cookie -> 200
  (f) restart app (new SessionMiddleware secret) -> old cookie no longer valid -> 302 to /login
"""

from __future__ import annotations

import pytest
import httpx


@pytest.fixture
def correct_passphrase() -> str:
    return "test-passphrase-03-05"


@pytest.fixture
def app_with_seeded_user(correct_passphrase):
    """Build a create_app() instance with a seeded user + passphrase set."""
    import os
    import asyncio
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    # Set the passphrase in the vault so /login POST can verify it
    _vault.set_passphrase(correct_passphrase)

    with patch("gekko.config.get_settings") as mock_settings_fn:
        settings = MagicMock()
        settings.gekko_user_id = "testuser"
        settings.dashboard_url = "http://localhost:8000"
        settings.db_path_for.return_value = ":memory:"
        mock_settings_fn.return_value = settings

        app = create_app()
    yield app, settings
    _vault.clear()


@pytest.mark.asyncio
async def test_get_login() -> None:
    """GET /login returns 200 with passphrase form."""
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("any")
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/login")
        assert resp.status_code == 200
        assert "passphrase" in resp.text.lower()
        assert "login-form" in resp.text or "Sign in" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_post_login_wrong_passphrase() -> None:
    """POST /login with wrong passphrase re-renders with .login-error, no Set-Cookie."""
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "the-real-passphrase"
    _vault.set_passphrase(correct)
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
                resp = await client.post(
                    "/login",
                    data={"passphrase": "wrong-passphrase", "next": "/approvals"},
                )
        assert resp.status_code == 200
        assert "login-error" in resp.text
        # No session cookie should be set on failure
        assert "gekko_session" not in resp.headers.get("set-cookie", "")
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_post_login_success() -> None:
    """POST /login with correct passphrase mints session cookie + 303 redirect."""
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "the-real-passphrase"
    _vault.set_passphrase(correct)
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
                resp = await client.post(
                    "/login",
                    data={"passphrase": correct, "next": "/approvals"},
                )
        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert "/approvals" in location
        # Session cookie should be minted
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gekko_session" in set_cookie
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_get_approvals_without_cookie_redirects_to_login() -> None:
    """GET /approvals without session cookie -> 302 to /login?next=/approvals."""
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

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
        # Should redirect to /login
        assert resp.status_code in (302, 303)
        location = resp.headers.get("location", "")
        assert "/login" in location
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_get_approvals_with_valid_cookie_returns_200() -> None:
    """GET /approvals with valid session cookie -> 200."""
    from unittest.mock import patch, MagicMock, AsyncMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "the-real-passphrase"
    _vault.set_passphrase(correct)
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            # Mock DB session so /approvals doesn't try to open a real DB
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.begin = MagicMock(return_value=mock_session)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
            mock_sf = MagicMock(return_value=mock_session)
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                # First login to get a cookie
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": correct, "next": "/approvals"},
                )
                assert login_resp.status_code == 303
                # Now GET /approvals with the cookie
                resp = await client.get("/approvals")

        assert resp.status_code == 200
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_restart_app_invalidates_old_cookie() -> None:
    """After app restart (new ephemeral secret), old session cookie is invalid (D-58)."""
    from unittest.mock import patch, MagicMock
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "the-real-passphrase"
    _vault.set_passphrase(correct)
    cookie_value = None
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            # First app instance — get a valid session cookie
            app1 = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app1),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": correct, "next": "/approvals"},
                )
                assert login_resp.status_code == 303
                set_cookie_header = login_resp.headers.get("set-cookie", "")
                assert "gekko_session" in set_cookie_header
                # Extract cookie value
                cookie_value = client.cookies.get("gekko_session")

            # Second app instance (simulated restart) — different secret
            app2 = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app2),
                base_url="http://test",
                follow_redirects=False,
            ) as client2:
                # Inject the old cookie manually
                if cookie_value:
                    client2.cookies.set("gekko_session", cookie_value)
                resp = await client2.get("/approvals")

        # Old cookie should be rejected by the new app's different secret
        assert resp.status_code in (302, 303)
        location = resp.headers.get("location", "")
        assert "/login" in location
    finally:
        _vault.clear()
