"""Dashboard form-edit round-trip — Plan 01-09 Task 3 (STRAT-02 + REG-04).

GET ``/strategies/{name}/edit`` populates the form with the latest
version. POST ``/strategies/{name}/save`` validates, calls
``next_version``, inserts a new StrategyRow scoped to the current user
(REG-04), and PRG-redirects back to GET. A second GET reflects the new
v2 values.

The test uses ``httpx.ASGITransport`` to drive the FastAPI app
in-process (no uvicorn). The lifespan is monkeypatched to point at a
``temp_sqlcipher_db`` engine; the real :func:`create_app` would
otherwise build an AlpacaFillStream + APScheduler + Slack singleton
against real env vars.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from gekko.db.engine import get_async_engine
from gekko.db.models import Base, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy

pytestmark = pytest.mark.integration


_PASSPHRASE = "test-dashboard-passphrase"
_USER_ID = "test-user"


@pytest.fixture
async def _seeded_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, Path, Any]]:
    """Build the FastAPI app with a stub lifespan + seed a v1 strategy.

    Yields ``(client, db_path, engine)`` so tests can inspect both the
    HTTP surface and the underlying DB.
    """
    monkeypatch.setenv("GEKKO_USER_ID", _USER_ID)
    monkeypatch.setenv("GEKKO_DATA_DIR", str(tmp_path))

    from gekko.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    db_path = settings.db_path_for(_USER_ID)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the engine + schema + seed a v1 strategy.
    engine = get_async_engine(db_path, _PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    seed_strategy = Strategy(
        strategy_id="strat-" + uuid4().hex,
        user_id=_USER_ID,
        name="edit-test",
        version=1,
        thesis="Original thesis: AI infra leaders.",
        watchlist=["NVDA", "AMD", "AVGO"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("200"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        mode="paper",
        schedule_time=None,
        created_at=datetime.now(UTC).isoformat(),
        created_by_chat=False,
    )

    sf = make_session_factory(engine)
    async with sf() as session, session.begin():
        now = datetime.now(UTC).isoformat()
        session.add(User(user_id=_USER_ID, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=seed_strategy.strategy_id,
                user_id=_USER_ID,
                strategy_name="edit-test",
                version=1,
                payload_json=seed_strategy.model_dump_json(),
                created_at=seed_strategy.created_at,
            )
        )

    # Build the FastAPI app WITHOUT a lifespan — httpx.ASGITransport
    # doesn't drive lifespan events, so we set ``app.state.engine``
    # directly. The real lifespan in gekko.dashboard.app pulls in the
    # Slack singleton + AlpacaFillStream + APScheduler which we don't
    # want under unit test.
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
            yield client, db_path, engine
        finally:
            await engine.dispose()


async def test_get_strategy_edit_renders_v1_form(
    _seeded_dashboard: tuple[httpx.AsyncClient, Path, Any]
) -> None:
    """GET /strategies/edit-test/edit returns 200 with the v1 fields populated."""
    client, _db_path, _engine = _seeded_dashboard
    resp = await client.get("/strategies/edit-test/edit")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Original thesis: AI infra leaders." in body
    assert "NVDA, AMD, AVGO" in body
    assert "v1" in body  # version label
    # Form action targets the save endpoint.
    assert 'action="/strategies/edit-test/save"' in body


async def test_get_strategy_edit_returns_404_for_unknown(
    _seeded_dashboard: tuple[httpx.AsyncClient, Path, Any]
) -> None:
    """REG-04 — unknown (or other-user) strategy names return 404."""
    client, _db_path, _engine = _seeded_dashboard
    resp = await client.get("/strategies/does-not-exist/edit")
    assert resp.status_code == 404, resp.text


async def test_post_strategy_save_creates_v2_and_redirects(
    _seeded_dashboard: tuple[httpx.AsyncClient, Path, Any]
) -> None:
    """STRAT-02 — POST persists v2 scoped to current_user and PRG-redirects."""
    client, _db_path, engine = _seeded_dashboard

    new_form = {
        "thesis": "Refined thesis: AI infra + clean energy.",
        "watchlist": "nvda, amd, avgo, plug",
        "max_position_pct": "0.04",
        "max_daily_loss_usd": "150",
        "max_trades_per_day": "2",
        "max_sector_exposure_pct": "0.20",
        "schedule_time": "10:00 America/New_York",
        "mode": "paper",
    }
    resp = await client.post(
        "/strategies/edit-test/save", data=new_form, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == "/strategies/edit-test/edit"

    # v2 row exists in the DB with the new fields, scoped to _USER_ID.
    sf = make_session_factory(engine)
    async with sf() as session:
        rows = (
            await session.execute(
                select(StrategyRow)
                .where(
                    StrategyRow.user_id == _USER_ID,
                    StrategyRow.strategy_name == "edit-test",
                )
                .order_by(StrategyRow.version.asc())
            )
        ).scalars().all()
    assert [r.version for r in rows] == [1, 2]

    v2 = Strategy.model_validate_json(rows[1].payload_json)
    assert v2.thesis == "Refined thesis: AI infra + clean energy."
    assert v2.watchlist == ["NVDA", "AMD", "AVGO", "PLUG"]
    assert v2.hard_caps.max_position_pct == Decimal("0.04")
    assert v2.hard_caps.max_daily_loss_usd == Decimal("150")
    assert v2.hard_caps.max_trades_per_day == 2
    assert v2.hard_caps.max_sector_exposure_pct == Decimal("0.20")
    assert v2.schedule_time == "10:00 America/New_York"

    # Follow the redirect and confirm the form reflects v2 values.
    follow = await client.get("/strategies/edit-test/edit")
    assert follow.status_code == 200, follow.text
    assert "Refined thesis: AI infra + clean energy." in follow.text
    assert "NVDA, AMD, AVGO, PLUG" in follow.text
    assert "v2" in follow.text


async def test_dashboard_renders_paper_banner_and_disclaimer(
    _seeded_dashboard: tuple[httpx.AsyncClient, Path, Any]
) -> None:
    """REG-01 — PAPER banner + 'Not investment advice' footer on every page."""
    client, _db_path, _engine = _seeded_dashboard
    resp = await client.get("/strategies")
    assert resp.status_code == 200
    body = resp.text
    assert "banner-paper" in body
    assert "PAPER" in body
    assert "Not investment advice" in body
