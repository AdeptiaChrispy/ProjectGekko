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
