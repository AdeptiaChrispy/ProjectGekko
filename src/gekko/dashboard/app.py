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

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

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
    from gekko.scheduler.jobs import build_scheduler
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

    # 2. Scheduler.
    app.state.scheduler = build_scheduler(app.state.sync_engine)
    app.state.scheduler.start()

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

    try:
        yield
    finally:
        log.info("dashboard.lifespan.shutdown", user_id=user_id)
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

    # Static (HTMX + Tailwind subset).
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Slack events endpoint — wraps slack-bolt's AsyncSlackRequestHandler.
    from gekko.slack.app import slack_handler

    @app.post("/slack/events")
    async def slack_events(req: Request) -> Response:
        return await slack_handler.handle(req)

    return app


__all__: tuple[str, ...] = ("create_app", "lifespan")
