"""Dashboard kill-switch routes — Plan 02-05 Task 2 (UI-SPEC §2b).

Covers the form-based POST /kill flow + /kill/confirm-modal + /modal/close
+ /unkill symmetric, against the FastAPI app via httpx.ASGITransport.

Mirrors the pattern in tests/integration/test_dashboard_strategy_edit.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest

from gekko.db.engine import get_async_engine
from gekko.db.models import Base, User
from gekko.db.session import make_session_factory

pytestmark = pytest.mark.integration

_PASSPHRASE = "test-dashboard-passphrase"
_USER_ID = "test-user"


@pytest.fixture
async def _dashboard_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    """Build the FastAPI app with a stub lifespan + seed a User row."""
    monkeypatch.setenv("GEKKO_USER_ID", _USER_ID)
    monkeypatch.setenv("GEKKO_DATA_DIR", str(tmp_path))

    from gekko.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    db_path = settings.db_path_for(_USER_ID)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = get_async_engine(db_path, _PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=_USER_ID,
                created_at=datetime.now(UTC).isoformat(),
                kill_active=False,
            )
        )

    # Wire kill_switch + executor seams so background tasks don't try to
    # build a real broker / hit Slack.
    from gekko.execution import executor, kill_switch as ks_mod

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )

    from unittest.mock import AsyncMock, MagicMock

    async def _noop_dm(uid: str, text: str) -> None:
        return None

    monkeypatch.setattr(executor, "_send_slack_dm", _noop_dm)

    broker = MagicMock()
    broker.get_orders_open = AsyncMock(return_value=[])
    broker.cancel_order = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    from fastapi import FastAPI
    from gekko.dashboard.routes import router

    app = FastAPI(title="Gekko (test)")
    app.include_router(router)
    app.state.engine = engine

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        try:
            yield client, engine
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# GET /kill/confirm-modal — returns the modal fragment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_confirm_modal_returns_modal_fragment(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """GET /kill/confirm-modal returns the kill_modal.html.j2 partial."""
    client, _engine = _dashboard_client
    resp = await client.get("/kill/confirm-modal")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Halt all trading" in body
    assert 'name="confirm"' in body
    # CSP: no inline <script> or onclick.
    assert "<script" not in body.lower()
    assert "onclick=" not in body.lower()


@pytest.mark.asyncio
async def test_unkill_confirm_modal_returns_modal_fragment(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """GET /unkill/confirm-modal returns the unkill_modal.html.j2 partial."""
    client, _engine = _dashboard_client
    resp = await client.get("/unkill/confirm-modal")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Resume trading" in body
    assert 'name="confirm"' in body
    assert "<script" not in body.lower()


@pytest.mark.asyncio
async def test_modal_close_returns_empty_html(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """GET /modal/close returns empty body to clear #modal-mount."""
    client, _engine = _dashboard_client
    resp = await client.get("/modal/close")
    assert resp.status_code == 200
    assert resp.text == ""


# ---------------------------------------------------------------------------
# POST /kill — typed-form validation + banner partial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_kill_with_correct_typed_returns_banner(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """POST /kill with confirm="KILL" fires background kill + returns banner."""
    client, _engine = _dashboard_client
    resp = await client.post("/kill", data={"confirm": "KILL"})
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "KILL ACTIVE" in body
    assert "banner-kill" in body
    assert 'role="alert"' in body
    assert 'aria-live="assertive"' in body


@pytest.mark.asyncio
async def test_post_kill_with_lowercase_typed_400s(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """POST /kill with confirm="kill" returns 400 (server-side gate)."""
    client, _engine = _dashboard_client
    resp = await client.post("/kill", data={"confirm": "kill"})
    assert resp.status_code == 400
    assert "KILL" in resp.text


@pytest.mark.asyncio
async def test_post_kill_with_wrong_typed_400s(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """POST /kill with confirm=arbitrary returns 400."""
    client, _engine = _dashboard_client
    resp = await client.post("/kill", data={"confirm": "stop-now"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_unkill_with_correct_typed_clears_banner(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """POST /unkill with confirm="UNKILL" returns the empty banner mount."""
    client, _engine = _dashboard_client
    resp = await client.post("/unkill", data={"confirm": "UNKILL"})
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert 'id="kill-banner-mount"' in body
    # Empty mount — no banner-kill class rendered.
    assert "banner-kill" not in body


@pytest.mark.asyncio
async def test_post_unkill_with_lowercase_typed_400s(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _engine = _dashboard_client
    resp = await client.post("/unkill", data={"confirm": "unkill"})
    assert resp.status_code == 400
    assert "UNKILL" in resp.text


# ---------------------------------------------------------------------------
# GET /kill/state — HTMX poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_state_returns_in_flight_fragment(
    _dashboard_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """GET /kill/state returns small text fragment."""
    client, _engine = _dashboard_client
    resp = await client.get("/kill/state")
    assert resp.status_code == 200
    # Either "Setting…" (kill not yet active) or "Kill ACTIVE…" (sweep complete).
    body = resp.text
    assert "kill_active" in body.lower() or "Kill ACTIVE" in body
