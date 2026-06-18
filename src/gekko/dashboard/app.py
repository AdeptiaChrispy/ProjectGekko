"""FastAPI dashboard application — Plan 01-09 Task 3.

Glue between every Phase 1 surface — the lifespan startup brings up
five things in order:

  1. **DB engines.** Per-user async + sync SQLCipher engines via
     :mod:`gekko.db.engine`. The SQLCipher passphrase comes from the
     :mod:`gekko.vault.passphrase` cache (operator-supplied by
     ``gekko serve`` BEFORE :func:`create_app` is called).
  2. **APScheduler.** :func:`gekko.scheduler.jobs.build_scheduler` over
     the sync engine; :meth:`AsyncIOScheduler.start` inside the
     lifespan so the running event loop is available.
  3. **AlpacaFillStream.** :class:`gekko.brokers.stream.AlpacaFillStream`
     constructed with ``on_fill=`` bound to
     :func:`gekko.execution.executor.on_fill_event` (the Plan 01-08
     callback) and started — fills will land in the audit log and DM
     the user.
  4. **Slack interactivity.** :mod:`gekko.slack.interactivity` is
     imported for side effects so its ``@app.action`` /
     ``@app.command`` handlers register against the Bolt singleton; the
     ``POST /slack/events`` route bridges incoming requests via
     :data:`gekko.slack.app.slack_handler`.
  5. **Static + routes.** ``/static/`` serves the vendored HTMX +
     hand-crafted Tailwind subset; :mod:`gekko.dashboard.routes`
     contributes the dashboard pages.

Shutdown reverses the order: scheduler -> fill-stream -> async engine
-> sync engine.

Pitfall 11: ``gekko serve`` runs uvicorn with ``workers=1`` and no
``--reload`` flag. Hot-reload re-runs the lifespan and can spawn
duplicate scheduler fires + fill-stream connections.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from gekko.config import get_settings
from gekko.dashboard.routes import router
from gekko.logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover
    pass

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown wiring.

    Importing the Slack singleton, building broker streams, and
    constructing the async engine all happen here (NOT at import time)
    so unit tests can monkeypatch the underlying pieces before any
    network / DB call fires.
    """
    from gekko.brokers.stream import AlpacaFillStream
    from gekko.db.engine import get_async_engine, get_sync_engine
    from gekko.execution.executor import on_fill_event
    from gekko.scheduler.jobs import build_scheduler, register_expire_stale_sweep
    from gekko.vault.passphrase import get_passphrase

    # gekko.slack.interactivity registers @slack_app.action handlers at
    # import time. Importing the module here (not at top of file) keeps
    # unit tests that don't exercise the full app from constructing the
    # Bolt singleton.
    import gekko.slack.interactivity  # noqa: F401  (side-effect import)

    settings = get_settings()
    user_id = settings.gekko_user_id
    passphrase = get_passphrase()
    db_path = settings.db_path_for(user_id)

    log.info("dashboard.lifespan.startup", user_id=user_id)

    # 1. Engines.
    app.state.engine = get_async_engine(db_path, passphrase)
    app.state.sync_engine = get_sync_engine(db_path, passphrase)

    # 1b. Boot-time kill_active check (D-36 / plan 02-05 Task 3).
    # Reads users.kill_active BEFORE the scheduler starts emitting jobs.
    # If True, DM the operator + set app.state.kill_active=True so the
    # banner renders on the first request without waiting for the FastAPI
    # dependency's TTL cache miss.
    app.state.kill_active = False
    app.state.kill_active_since = None
    try:
        from sqlalchemy import select as _select

        from gekko.db.models import User
        from gekko.db.session import make_session_factory

        sf_boot = make_session_factory(app.state.engine)
        async with sf_boot() as session:
            row = (
                await session.execute(
                    _select(User).where(User.user_id == user_id)
                )
            ).scalar_one_or_none()
            if row is not None and row.kill_active:
                app.state.kill_active = True
                app.state.kill_active_since = row.kill_active_since
                log.warning(
                    "dashboard.lifespan.kill_active_on_restart",
                    user_id=user_id,
                    kill_active_since=row.kill_active_since,
                )
                try:
                    from gekko.execution.executor import _send_slack_dm

                    await _send_slack_dm(
                        user_id,
                        "🚫 Restarted with kill_active=ON; no orders will "
                        "fire until `/gekko unkill CONFIRM`.",
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "dashboard.lifespan.kill_active_dm_failed",
                        user_id=user_id,
                    )
    except Exception:  # noqa: BLE001
        log.exception(
            "dashboard.lifespan.kill_active_check_failed",
            user_id=user_id,
        )

    # 2. Scheduler.
    app.state.scheduler = build_scheduler(app.state.sync_engine)
    app.state.scheduler.start()

    # 2a. Register the 60-second stale-proposal expiry sweep (HITL-03 / Plan 03-04).
    # Placed AFTER scheduler.start() so replace_existing=True can check the jobstore.
    register_expire_stale_sweep(app.state.scheduler, user_id=user_id)

    # 3. AlpacaFillStream — wires the executor's on_fill_event callback.
    async def _on_fill(payload: dict) -> None:
        await on_fill_event(payload, user_id=user_id)

    app.state.fill_stream = AlpacaFillStream(
        api_key=settings.alpaca_paper_api_key.get_secret_value(),
        secret_key=settings.alpaca_paper_secret_key.get_secret_value(),
        user_id=user_id,
        on_fill=_on_fill,
    )
    app.state.fill_stream.start()

    # 4. Slack Socket Mode (when SLACK_APP_TOKEN is set).
    #
    # Opens an outbound WebSocket to Slack so interactivity events flow
    # WITHOUT a public Request URL / tunnel. The HTTP `POST /slack/events`
    # route stays mounted for the tunnel-based deployment path, but it
    # goes unused when socket mode is active.
    app.state.slack_socket_handler = None
    # Treat empty `SLACK_APP_TOKEN=` env value as "not configured" — pydantic
    # constructs a SecretStr("") for an empty assignment, which would
    # otherwise look truthy via `is not None`.
    slack_app_token_value = (
        settings.slack_app_token.get_secret_value().strip()
        if settings.slack_app_token is not None
        else ""
    )
    if slack_app_token_value:
        from slack_bolt.adapter.socket_mode.async_handler import (
            AsyncSocketModeHandler,
        )

        from gekko.slack.app import slack_app

        app.state.slack_socket_handler = AsyncSocketModeHandler(
            slack_app, slack_app_token_value
        )
        await app.state.slack_socket_handler.connect_async()
        log.info("dashboard.slack.socket_mode_connected")

    try:
        yield
    finally:
        log.info("dashboard.lifespan.shutdown", user_id=user_id)
        if app.state.slack_socket_handler is not None:
            try:
                await app.state.slack_socket_handler.close_async()
            except Exception:  # noqa: BLE001
                log.exception("dashboard.slack.socket_mode_close_failed")
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            log.exception("dashboard.scheduler.shutdown_failed")
        try:
            await app.state.fill_stream.stop()
        except Exception:  # noqa: BLE001
            log.exception("dashboard.fill_stream.stop_failed")
        try:
            await app.state.engine.dispose()
        except Exception:  # noqa: BLE001
            log.exception("dashboard.engine.dispose_failed")
        try:
            app.state.sync_engine.dispose()
        except Exception:  # noqa: BLE001
            log.exception("dashboard.sync_engine.dispose_failed")


def create_app() -> FastAPI:
    """Build the FastAPI app. Called by ``gekko serve`` AFTER the operator's
    passphrase is cached.

    Tests can call :func:`create_app` directly; the lifespan is what
    they monkeypatch (or the relevant state attributes) — see
    ``tests/integration/test_dashboard_strategy_edit.py``.
    """
    app = FastAPI(title="Gekko", lifespan=lifespan)
    app.include_router(router)

    # D-57 / D-58 — SessionMiddleware for /login passphrase-cookie auth.
    # Registered via add_middleware BEFORE the @app.middleware("http") decorator
    # below. Per Starlette's reverse-execution rule, add_middleware inserts
    # to the OUTERMOST position (runs first on request ingress, last on
    # response egress) so `request.session` is available to every inner
    # middleware and route handler.
    # D-58: ephemeral per-restart secret via os.urandom(32).hex() so a
    # leaked cookie from one server run is dead after restart.
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.urandom(32).hex(),
        max_age=8 * 3600,  # 8-hour idle expiry per D-57
        https_only=False,  # HTTP-on-localhost OK per D-57
        same_site="strict",  # SameSite=Strict per D-57
        session_cookie="gekko_session",
    )

    # Static (HTMX + Tailwind subset).
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Slack events endpoint — wraps slack-bolt's AsyncSlackRequestHandler.
    from gekko.slack.app import slack_handler

    @app.post("/slack/events")
    async def slack_events(req: Request) -> Response:
        return await slack_handler.handle(req)

    # Plan 02-06 Task 3 — banner_mode + kill_active middleware.
    # Copies the app.state values to request.state so the Jinja base
    # template can render the right banner stack (live on top, kill
    # second, paper as fallback). The actual cache invalidation +
    # 60s TTL lives in the route handlers that flip these — this
    # middleware is the read side only.
    @app.middleware("http")
    async def _inject_banner_state(request: Request, call_next: Any) -> Any:
        try:
            request.state.kill_active = getattr(
                request.app.state, "kill_active", False
            )
        except Exception:  # noqa: BLE001 — best-effort
            request.state.kill_active = False
        try:
            request.state.banner_mode = await _resolve_banner_mode(request)
        except Exception:  # noqa: BLE001
            request.state.banner_mode = "PAPER"
        return await call_next(request)

    return app


async def _resolve_banner_mode(request: Request) -> str:
    """Compute ``banner_mode`` for the current request.

    Checks (in order):
      1. ``request.app.state.banner_mode`` if a route set it explicitly
         (e.g., /promote-to-live writes "LIVE" so the next render picks
         it up without waiting for the TTL).
      2. Counts ``strategy_metadata.live_mode_eligible=True`` rows for
         the current user. If ≥1, "LIVE"; else "PAPER".

    Cached for 60s on ``app.state._banner_mode_ts`` per UI-SPEC §1.
    """
    import time as _time

    from sqlalchemy import func, select

    from gekko.config import get_settings
    from gekko.db.models import StrategyMetadata
    from gekko.db.session import make_session_factory

    app = request.app
    now = _time.time()
    cached_ts = getattr(app.state, "_banner_mode_ts", 0.0)
    cached_mode = getattr(app.state, "banner_mode", None)
    if cached_mode is not None and (now - cached_ts) < 60.0:
        return cached_mode

    settings = get_settings()
    user_id = settings.gekko_user_id
    engine = getattr(app.state, "engine", None)
    if engine is None:
        return "PAPER"

    sf = make_session_factory(engine)
    async with sf() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(StrategyMetadata)
                .where(
                    StrategyMetadata.user_id == user_id,
                    StrategyMetadata.live_mode_eligible.is_(True),
                )
            )
        ).scalar_one()
    mode = "LIVE" if count and count >= 1 else "PAPER"
    app.state.banner_mode = mode
    app.state._banner_mode_ts = now
    return mode


__all__: tuple[str, ...] = ("create_app", "lifespan")
