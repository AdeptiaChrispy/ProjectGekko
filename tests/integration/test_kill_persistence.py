"""Kill-state cross-restart persistence — Plan 02-05 (D-36 / EXEC-06).

After ``_execute_kill`` writes ``users.kill_active=True``, a process restart
(simulated by disposing the engine + re-reading via the same DB file) MUST
see the column still True. The kill flag NEVER auto-clears.

Plan 02-05 Task 3 adds the FastAPI lifespan boot-time DM hook on top of
this — that test lives in ``tests/integration/test_kill_lifespan.py`` (and
is added in Task 3). This file covers the column-survives-restart axis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.brokers.base import Brokerage
from gekko.db.engine import get_async_engine
from gekko.db.models import Base, User
from gekko.db.session import make_session_factory


pytestmark = pytest.mark.integration

_TEST_PASSPHRASE = "test-passphrase"  # nosec: test-only literal


@pytest_asyncio.fixture
async def restartable_db(tmp_path: Path) -> Any:
    """Yield a DB path that survives engine.dispose() + re-open.

    Unlike the ``temp_sqlcipher_db`` fixture (which yields the engine), this
    yields the path so the test can dispose + re-open to simulate restart.
    """
    db_path = tmp_path / "test-restart.db"
    engine = get_async_engine(db_path, _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return db_path


def _broker_with_orders(n: int) -> MagicMock:
    broker = MagicMock(spec=Brokerage)
    broker.get_orders_open = AsyncMock(
        return_value=[{"id": f"ord-{i}"} for i in range(n)]
    )
    broker.cancel_order = AsyncMock(return_value=True)
    broker.cancel_all_open_orders = AsyncMock(return_value=[])
    return broker


@pytest.mark.asyncio
async def test_kill_state_survives_process_restart(
    restartable_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_active=True written by ``_execute_kill`` survives engine cycle.

    Simulates the cross-restart scenario:
      1. Cold boot — seed User row with kill_active=False
      2. Run _execute_kill — column becomes True
      3. Dispose engine (simulates `gekko serve` Ctrl-C)
      4. Re-open engine on the same DB file (simulates restart)
      5. SELECT — column STILL True
    """
    from gekko.audit import log as _audit_log
    from gekko.execution import executor, kill_switch as ks_mod

    _audit_log._append_locks.clear()

    user_id = "test-user"

    # ---- Cold boot: seed the User row.
    engine_1 = get_async_engine(restartable_db, _TEST_PASSPHRASE)
    sf_1 = make_session_factory(engine_1)
    now = datetime.now(UTC).isoformat()
    async with sf_1() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=False,
            )
        )

    # Wire kill_switch + executor seams to the cold-boot engine.
    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf_1, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf_1, None)
    )

    async def _noop_dm(uid: str, text: str) -> None:
        return None

    monkeypatch.setattr(executor, "_send_slack_dm", _noop_dm)

    broker = _broker_with_orders(0)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    # ---- Issue the kill.
    await ks_mod._execute_kill(
        user_id=user_id, source="cli", reason="restart-test"
    )

    # ---- Sanity-check the column is True PRE-restart.
    async with sf_1() as session:
        row = (
            await session.execute(
                select(User).where(User.user_id == user_id)
            )
        ).scalar_one()
        assert row.kill_active is True
        assert row.kill_active_reason == "restart-test"

    # ---- Simulate restart: dispose engine + re-open.
    await engine_1.dispose()

    engine_2 = get_async_engine(restartable_db, _TEST_PASSPHRASE)
    sf_2 = make_session_factory(engine_2)
    try:
        async with sf_2() as session:
            row = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).scalar_one()
            # The load-bearing assertion: kill state SURVIVED the restart.
            assert row.kill_active is True, (
                "D-36 persistence invariant violated: kill_active was "
                "cleared by engine.dispose() + re-open cycle"
            )
            assert row.kill_active_reason == "restart-test"
            assert row.kill_active_since is not None
    finally:
        await engine_2.dispose()


# ---------------------------------------------------------------------------
# FastAPI lifespan boot-time DM (Plan 02-05 Task 3 / D-36)
# ---------------------------------------------------------------------------


async def _setup_lifespan_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_dir: Path,
    user_id: str,
) -> None:
    """Seed env + passphrase + stub heavy lifespan side effects."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-alpaca-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-alpaca-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST_USER")
    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("GEKKO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEKKO_DB_PASSPHRASE", _TEST_PASSPHRASE)

    from gekko.config import get_settings

    get_settings.cache_clear()
    from gekko.vault.passphrase import set_passphrase

    set_passphrase(_TEST_PASSPHRASE)

    # Stub sync engine (the lifespan only needs it for the scheduler stub).
    from gekko.db import engine as _engine_mod

    monkeypatch.setattr(
        _engine_mod, "get_sync_engine", lambda _p, _pw: MagicMock()
    )

    class _StubScheduler:
        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

    from gekko.scheduler import jobs as _jobs_mod

    monkeypatch.setattr(
        _jobs_mod, "build_scheduler", lambda _s: _StubScheduler()
    )

    class _StubFillStream:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    from gekko.brokers import stream as _stream_mod

    monkeypatch.setattr(_stream_mod, "AlpacaFillStream", _StubFillStream)


@pytest.mark.asyncio
async def test_lifespan_boot_time_kill_active_dms_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When users.kill_active=True at startup, lifespan DMs + sets app.state.

    Simulates the cross-restart scenario at the FastAPI app level:
      1. Seed user row with kill_active=True (simulates a prior kill)
      2. Invoke the FastAPI lifespan against this DB
      3. Assert: app.state.kill_active=True AND _send_slack_dm was called
         with the "Restarted with kill_active=ON" boot DM
    """
    user_id = "test-user"
    data_dir = tmp_path / "gekko-data"
    data_dir.mkdir()
    db_path = data_dir / f"{user_id}.db"

    engine = get_async_engine(db_path, _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=True,
                kill_active_since=now,
                kill_active_reason="prior-process",
            )
        )
    await engine.dispose()

    await _setup_lifespan_env(monkeypatch, data_dir=data_dir, user_id=user_id)

    # Capture the boot-time DM.
    dms: list[tuple[str, str]] = []

    async def _capture_dm(uid: str, text: str) -> None:
        dms.append((uid, text))

    from gekko.execution import executor

    monkeypatch.setattr(executor, "_send_slack_dm", _capture_dm)

    # Build the app + drive the lifespan.
    from fastapi import FastAPI
    from gekko.dashboard import app as dashboard_app

    app = FastAPI(lifespan=dashboard_app.lifespan)

    async with app.router.lifespan_context(app):
        assert app.state.kill_active is True, (
            "lifespan must set app.state.kill_active=True when DB column is True"
        )
        assert app.state.kill_active_since is not None
        assert len(dms) == 1, (
            f"expected boot-time kill DM, got {len(dms)}: {dms!r}"
        )
        uid, text = dms[0]
        assert uid == user_id
        assert "Restarted with kill_active=ON" in text
        assert "unkill" in text.lower()


@pytest.mark.asyncio
async def test_lifespan_no_dm_when_kill_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When kill_active=False at startup, lifespan DOES NOT DM."""
    user_id = "test-user"
    data_dir = tmp_path / "gekko-data"
    data_dir.mkdir()
    db_path = data_dir / f"{user_id}.db"

    engine = get_async_engine(db_path, _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=False,
            )
        )
    await engine.dispose()

    await _setup_lifespan_env(monkeypatch, data_dir=data_dir, user_id=user_id)

    dms: list[tuple[str, str]] = []

    async def _capture_dm(uid: str, text: str) -> None:
        dms.append((uid, text))

    from gekko.execution import executor

    monkeypatch.setattr(executor, "_send_slack_dm", _capture_dm)

    from fastapi import FastAPI
    from gekko.dashboard import app as dashboard_app

    app = FastAPI(lifespan=dashboard_app.lifespan)
    async with app.router.lifespan_context(app):
        assert app.state.kill_active is False
        assert dms == [], (
            f"expected NO DM when kill_active=False, got {dms!r}"
        )


@pytest.mark.asyncio
async def test_kill_state_cleared_only_by_unkill(
    restartable_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_active=False ONLY after _execute_unkill — never auto-cleared."""
    from gekko.audit import log as _audit_log
    from gekko.execution import executor, kill_switch as ks_mod

    _audit_log._append_locks.clear()

    user_id = "test-user"
    engine = get_async_engine(restartable_db, _TEST_PASSPHRASE)
    sf = make_session_factory(engine)

    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=False,
            )
        )

    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )

    async def _noop_dm(uid: str, text: str) -> None:
        return None

    monkeypatch.setattr(executor, "_send_slack_dm", _noop_dm)

    broker = _broker_with_orders(0)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    try:
        await ks_mod._execute_kill(
            user_id=user_id, source="cli", reason="test"
        )
        # is_active stays True after multiple SELECTs.
        for _ in range(3):
            assert await ks_mod.is_active(user_id) is True

        # Only _execute_unkill clears it.
        await ks_mod._execute_unkill(user_id=user_id, source="cli")
        assert await ks_mod.is_active(user_id) is False
    finally:
        await engine.dispose()
