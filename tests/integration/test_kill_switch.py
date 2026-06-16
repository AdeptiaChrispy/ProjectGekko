"""Kill-switch integration tests — Plan 02-05 Task 1 (EXEC-06 / D-37).

End-to-end-ish: real SQLCipher engine + real ``_execute_kill`` + MagicMock
broker. Asserts the 5-second SLA on a populated cancel sweep + the
parallel-cancel via asyncio.gather (NOT sequential).

Per VALIDATION.md the wall-clock 5s SLA is a MANUAL verification (Task 4
operator demo). This file covers the automated bound: the orchestrator
runs ``asyncio.gather(*cancels, timeout=4.0)`` and writes the audit-event
pair within an in-memory test run.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.brokers.base import Brokerage
from gekko.db.models import Event, User
from gekko.db.session import make_session_factory


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def kill_seam(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """Wire the kill_switch + executor seams to the in-memory engine."""
    from gekko.audit import log as _audit_log
    from gekko.execution import executor, kill_switch as ks_mod

    _audit_log._append_locks.clear()
    sf = make_session_factory(temp_sqlcipher_db)
    monkeypatch.setattr(
        ks_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )

    dms: list[tuple[str, str]] = []

    async def _fake_dm(uid: str, text: str) -> None:
        dms.append((uid, text))

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)

    ns = type("KillSeamNS", (), {})()
    ns.sf = sf
    ns.dms = dms
    return ns


async def _seed_user(sf: Any, user_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=False,
            )
        )


def _broker_with_orders(n: int, *, per_cancel_delay: float = 0.0) -> MagicMock:
    """Build a MagicMock broker with N open orders."""
    broker = MagicMock(spec=Brokerage)
    broker.get_orders_open = AsyncMock(
        return_value=[{"id": f"ord-{i}"} for i in range(n)]
    )

    async def _cancel(order_id: str) -> bool:
        if per_cancel_delay:
            await asyncio.sleep(per_cancel_delay)
        return True

    broker.cancel_order = AsyncMock(side_effect=_cancel)
    broker.cancel_all_open_orders = AsyncMock(return_value=[])
    return broker


# ---------------------------------------------------------------------------
# 5-second SLA — automated bound (manual wall-clock demo lives in Task 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_completes_under_5s_with_5_open_orders(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 open orders, each cancel ~0.5s → parallel kill < 5s end-to-end."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _broker_with_orders(5, per_cancel_delay=0.5)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    start = time.monotonic()
    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )
    elapsed = time.monotonic() - start

    assert tally["cancelled"] == 5
    assert tally["total"] == 5
    assert elapsed < 5.0, (
        f"kill switch SLA breach: elapsed {elapsed:.2f}s > 5s "
        f"(5 × 0.5s parallel cancels should land in <1s)"
    )


# ---------------------------------------------------------------------------
# Parallel cancel — NOT sequential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_cancels_in_parallel_via_asyncio_gather(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10 cancels × 0.3s parallel = ~0.3s; sequential would be 3.0s."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _broker_with_orders(10, per_cancel_delay=0.3)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    start = time.monotonic()
    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )
    elapsed = time.monotonic() - start

    assert tally["cancelled"] == 10
    # Sequential would be 10 × 0.3 = 3.0s; parallel should be ~0.3-0.6s.
    assert elapsed < 1.5, (
        f"cancels appear sequential ({elapsed:.2f}s for 10 × 0.3s) — "
        "kill_switch.py must use asyncio.gather"
    )


# ---------------------------------------------------------------------------
# Audit-event pair lands in the chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_audit_chain_intact_after_kill(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After kill: 2 kill_switch events; walk_chain returns no breaks."""
    from gekko.audit.verify import walk_chain
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _broker_with_orders(2)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    async with kill_seam.sf() as session:
        events = (
            await session.execute(select(Event).order_by(Event.id.asc()))
        ).scalars().all()
        types = [e.event_type for e in events]
        broken = await walk_chain(session, user_id)

    assert types == ["kill_switch", "kill_switch"]
    assert broken == [], (
        f"audit chain broken after kill: {broken!r}"
    )


# ---------------------------------------------------------------------------
# After kill: subsequent place_order via OrderGuard rejects with kill_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_kill_orderguard_rejects_new_place_order(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: after _execute_kill, check_kill_switch (plan 02-02) refuses."""
    from gekko.core.errors import OrderGuardRejected
    from gekko.execution import kill_switch as ks_mod
    from gekko.execution.checks import _kill_switch as ks_check_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    # Wire the check_kill_switch seam to the same engine.
    monkeypatch.setattr(
        ks_check_mod, "_get_session_factory", lambda _u: (kill_seam.sf, None)
    )

    broker = _broker_with_orders(0)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    from gekko.execution.checks._kill_switch import check_kill_switch

    with pytest.raises(OrderGuardRejected) as excinfo:
        await check_kill_switch(user_id)

    assert excinfo.value.reject_code == "kill_active"
