"""Kill-switch read-side check — Plan 02-02 Task 1 (D-35 / EXEC-06 read half).

OrderGuard reads ``users.kill_active`` at every ``place_order``; when True,
NO order ever reaches the broker. The WRITE side (Slack ``/gekko kill``,
dashboard KILL button, cancel-open-orders SLA) lands in plan 02-05 — this
plan ships only the read half so the column added by plan 02-01 Task 4 has
an active enforcement gate.

Module-level ``_get_session_factory`` test seam (PATTERNS §3c) — verbatim
copy of :func:`gekko.execution.executor._get_session_factory` so tests can
monkeypatch this module's seam independently of the executor's. The
``finally: if engine is not None: await engine.dispose()`` pattern is
load-bearing per PATTERNS §3c — tests pass ``(pre_built_factory, None)``
to opt out of disposal.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.config import get_settings
from gekko.core.errors import OrderGuardRejected
from gekko.db.engine import get_async_engine
from gekko.db.models import User
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.vault.passphrase import get_passphrase as _get_passphrase


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors :func:`gekko.execution.executor._get_session_factory` exactly so
    tests have a per-module monkeypatch seam — patching the executor's seam
    does NOT also patch this one (each check owns its own engine lifecycle).
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


async def check_kill_switch(user_id: str) -> None:
    """Reject when ``users.kill_active`` is True for ``user_id``.

    :param user_id: The per-user SQLCipher DB scope.
    :raises OrderGuardRejected: With ``reject_code='kill_active'`` when the
        user row's ``kill_active`` column is True.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).scalar_one_or_none()
            if row is not None and row.kill_active:
                raise OrderGuardRejected(
                    "kill_active",
                    "Kill switch is ON; no orders will fire until /gekko unkill",
                    extra={"user_id": user_id},
                )
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = ("check_kill_switch",)
