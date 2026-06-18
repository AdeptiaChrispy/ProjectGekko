"""Regression tests for CR-01: unauthenticated access to safety-critical routes.

Plan 03-08 Task 1 + Task 2 — router-level require_session dependency.

Verifies:
  - All safety-critical routes return 302/307 redirect to /login when accessed
    without a valid session cookie (fail-closed auth gate).
  - GET /login and GET /healthz remain public (no session required).
  - Positive-control: /approvals was already gated and continues to redirect.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> object:
    """Create the FastAPI app with mocked settings (no real DB / Slack)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("test-passphrase-cr01")

    with patch("gekko.config.get_settings") as mock_fn:
        settings = MagicMock()
        settings.gekko_user_id = "testuser"
        settings.dashboard_url = "http://localhost:8000"
        mock_fn.return_value = settings

        app = create_app()
    return app


def _clear_vault() -> None:
    import gekko.vault.passphrase as _vault

    _vault.clear()


# ---------------------------------------------------------------------------
# Parametrized: unauthenticated → 302/307 for all safety-critical routes
# ---------------------------------------------------------------------------


_SAFETY_ROUTES: list[tuple[str, str, dict | None]] = [
    # (method, path, form_data_or_None)
    ("GET", "/strategies", None),
    ("GET", "/strategies/alpha/edit", None),
    ("POST", "/strategies/alpha/save", {
        "thesis": "x",
        "watchlist": "AAPL",
        "max_position_pct": "0.05",
        "max_daily_loss_usd": "200",
        "max_trades_per_day": "3",
        "max_sector_exposure_pct": "0.25",
        "mode": "paper",
    }),
    ("POST", "/trigger/alpha", None),
    # POST /kill: form body to pass form-validation before the auth check fires.
    # With router-level auth, the dependency runs BEFORE form parsing, so the
    # unauthenticated caller never reaches form validation.
    ("POST", "/kill", {"confirm": "KILL"}),
    ("POST", "/unkill", {"confirm": "UNKILL"}),
    ("GET", "/kill/state", None),
    ("POST", "/strategies/alpha/promote-to-live", {"strategy_name_confirm": "alpha"}),
    ("GET", "/live-confirm/fake-id", None),
    ("POST", "/live-confirm/fake-id", {
        "ack_real_money": "on",
        "ack_read_rationale": "on",
        "page_load_ts": "1000.0",
    }),
    # Positive control: /approvals was already gated in Plan 03-05 — must stay gated.
    ("GET", "/approvals", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,form_data", _SAFETY_ROUTES, ids=[
    f"{m}_{p.replace('/', '_').strip('_')}" for m, p, _ in _SAFETY_ROUTES
])
async def test_unauthenticated_redirects_to_login(
    method: str, path: str, form_data: dict | None
) -> None:
    """Unauthenticated access to a safety-critical route must redirect to /login.

    CR-01 regression: router-level Depends(require_session) gates ALL routes
    except /login and /healthz.
    """
    try:
        app = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            if method == "GET":
                resp = await client.get(path)
            else:
                resp = await client.post(path, data=form_data or {})

        assert resp.status_code in (302, 307), (
            f"{method} {path} expected 302/307 redirect, got {resp.status_code}. "
            f"Location: {resp.headers.get('location', '<none>')}"
        )
        location = resp.headers.get("location", "")
        assert location.startswith("/login"), (
            f"{method} {path}: redirect location {location!r} must start with /login"
        )
    finally:
        _clear_vault()


# ---------------------------------------------------------------------------
# Public-route positive controls — must NOT require session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_page_is_public() -> None:
    """GET /login must return 200 without any session cookie (public route)."""
    try:
        app = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/login")
        assert resp.status_code == 200, (
            f"GET /login returned {resp.status_code}, expected 200 (public route)"
        )
    finally:
        _clear_vault()


@pytest.mark.asyncio
async def test_healthz_is_public() -> None:
    """GET /healthz must return 200 without any session cookie (liveness probe)."""
    try:
        app = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200, (
            f"GET /healthz returned {resp.status_code}, expected 200 (public route)"
        )
    finally:
        _clear_vault()


# ---------------------------------------------------------------------------
# Authenticated session proceeds past auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticated_approvals_passes_auth() -> None:
    """GET /approvals with a valid session must pass the auth gate (200 response).

    CR-01 positive control: authenticated callers must NOT be redirected to
    /login. We use /approvals (gated since Plan 03-05) as the canonical
    proof that the require_session dependency yields correctly for valid
    sessions.
    """
    from unittest.mock import AsyncMock

    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-cr01-auth"
    _vault.set_passphrase(correct)
    try:
        with patch("gekko.config.get_settings") as mock_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_fn.return_value = settings

            # Return a mock session factory so /approvals doesn't open a real DB.
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sf = MagicMock(return_value=mock_session)
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                # Login first to mint a session cookie.
                login_resp = await client.post(
                    "/login",
                    data={"passphrase": correct, "next": "/approvals"},
                )
                assert login_resp.status_code == 303

                # GET /approvals with the session cookie — must succeed.
                resp = await client.get("/approvals")

        assert resp.status_code == 200, (
            f"Authenticated GET /approvals returned {resp.status_code} "
            f"(location: {resp.headers.get('location', '<none>')}); "
            "expected 200 — auth gate must yield for valid sessions"
        )
    finally:
        _vault.clear()
