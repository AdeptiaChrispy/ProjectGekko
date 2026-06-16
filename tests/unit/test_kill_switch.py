"""kill_switch.py unit tests — Plan 02-05 Task 1 (EXEC-06 / D-35 / D-36 / D-37).

Covers:

* Brokerage ABC: ``get_orders_open`` + ``cancel_all_open_orders`` are abstract
* AlpacaBroker: ``get_orders_open`` IS @retry-decorated; ``cancel_all_open_orders``
  is NOT (RESEARCH §6 Open Question #1)
* ``_execute_kill`` ordering invariant: DB write FIRST, then cancel sweep
* Audit-event pair: ``action="kill"`` + ``action="kill_complete"`` with tally
* ``asyncio.wait_for`` with timeout=4.0 in source
* Parallel cancel via ``asyncio.gather``
* Timeout fallback: pending = total when wait_for times out
* DM routes through executor._send_slack_dm seam (identity-split safe)
* ``_execute_unkill`` clears column + writes "unkill" event
* ``is_active`` reads DB fresh (no in-memory cache)
* No ``claude_agent_sdk`` import in kill_switch.py
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.brokers.base import Brokerage
from gekko.db.models import Event, User
from gekko.db.session import make_session_factory


# ---------------------------------------------------------------------------
# Brokerage ABC contract — Task 1 done criteria
# ---------------------------------------------------------------------------


def test_brokerage_abc_has_get_orders_open() -> None:
    """Brokerage ABC declares ``get_orders_open`` as abstract."""
    assert hasattr(Brokerage, "get_orders_open")
    assert (
        "get_orders_open" in Brokerage.__abstractmethods__
    ), (
        "get_orders_open must be marked @abstractmethod on Brokerage ABC"
    )


def test_brokerage_abc_has_cancel_all_open_orders() -> None:
    """Brokerage ABC declares ``cancel_all_open_orders`` as abstract."""
    assert hasattr(Brokerage, "cancel_all_open_orders")
    assert (
        "cancel_all_open_orders" in Brokerage.__abstractmethods__
    ), (
        "cancel_all_open_orders must be marked @abstractmethod on Brokerage ABC"
    )


# ---------------------------------------------------------------------------
# AlpacaBroker decoration policy (RESEARCH §6 Open Question #1)
# ---------------------------------------------------------------------------


def test_alpaca_get_orders_open_is_retry_decorated() -> None:
    """``AlpacaBroker.get_orders_open`` is a GET — IS decorated with retry."""
    from gekko.brokers.alpaca import AlpacaBroker

    assert hasattr(AlpacaBroker.get_orders_open, "__wrapped__"), (
        "AlpacaBroker.get_orders_open must carry @retry_on_rate_limit "
        "(it's a GET — EXEC-08)"
    )


def test_alpaca_cancel_all_open_orders_is_not_retry_decorated() -> None:
    """``AlpacaBroker.cancel_all_open_orders`` is NOT decorated.

    RESEARCH §6 Open Question #1 verbatim: kill timing trumps 429 resilience.
    The kill switch's ``asyncio.gather`` + 4s ``wait_for`` is the failure-
    tolerant scaffold.
    """
    from gekko.brokers.alpaca import AlpacaBroker

    assert not hasattr(AlpacaBroker.cancel_all_open_orders, "__wrapped__"), (
        "AlpacaBroker.cancel_all_open_orders must NOT carry "
        "@retry_on_rate_limit (RESEARCH §6 Open Question #1)"
    )


def test_alpaca_cancel_all_open_orders_has_zero_decorators_ast() -> None:
    """AST gate: AlpacaBroker.cancel_all_open_orders has zero decorators."""
    import gekko.brokers.alpaca as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "AlpacaBroker"
    )
    method = next(
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "cancel_all_open_orders"
    )
    assert method.decorator_list == [], (
        "AlpacaBroker.cancel_all_open_orders has unexpected decorators "
        "(RESEARCH §6 Open Question #1): "
        f"{[ast.dump(d) for d in method.decorator_list]!r}"
    )


# ---------------------------------------------------------------------------
# kill_switch module — no claude_agent_sdk import
# ---------------------------------------------------------------------------


def test_kill_switch_module_does_not_import_agent_sdk() -> None:
    """The kill_switch module must NOT import claude_agent_sdk.

    Mirrors the Phase-1 grep gate from Plan 01-08 over executor.py. The
    kill switch operates inside the deterministic firewall — no LLM bytes
    touch it.
    """
    import gekko.execution.kill_switch as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in src, (
        "src/gekko/execution/kill_switch.py must NOT mention "
        "'claude_agent_sdk' anywhere (deterministic-firewall invariant)"
    )


def test_kill_switch_wait_for_timeout_in_source() -> None:
    """Source-bytes check: ``asyncio.wait_for(..., timeout=4.0)`` is present.

    Required-done criterion from Plan 02-05 Task 1: the 4s budget is
    locked in source.
    """
    import gekko.execution.kill_switch as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "timeout=4.0" in src, (
        "kill_switch.py must contain literal 'timeout=4.0' "
        "(D-37 5s SLA invariant)"
    )
    assert "asyncio.wait_for" in src, (
        "kill_switch.py must use asyncio.wait_for to enforce the timeout"
    )


# ---------------------------------------------------------------------------
# _execute_kill behavioral tests — ordering, audit, tally, DM
# ---------------------------------------------------------------------------


async def _seed_user(sf: Any, user_id: str, *, kill_active: bool = False) -> None:
    """Insert a User row with the requested kill state."""
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now,
                kill_active=kill_active,
            )
        )


def _make_broker_with_open_orders(
    open_orders: list[dict[str, Any]],
    *,
    cancel_succeeds: bool = True,
    cancel_delay: float = 0.0,
) -> MagicMock:
    """Build a MagicMock Brokerage whose get_orders_open / cancel_order are wired."""
    broker = MagicMock(spec=Brokerage)
    broker.name = "alpaca"
    broker.is_paper = True
    broker.get_orders_open = AsyncMock(return_value=list(open_orders))

    async def _cancel(order_id: str) -> bool:
        if cancel_delay:
            await asyncio.sleep(cancel_delay)
        return cancel_succeeds

    broker.cancel_order = AsyncMock(side_effect=_cancel)
    broker.cancel_all_open_orders = AsyncMock(return_value=[])
    return broker


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

    # Stub the Slack DM seam so unit tests don't hit Slack.
    dms: list[tuple[str, str]] = []

    async def _fake_dm(uid: str, text: str) -> None:
        dms.append((uid, text))

    monkeypatch.setattr(executor, "_send_slack_dm", _fake_dm)

    ns = type("KillSeamNS", (), {})()
    ns.sf = sf
    ns.dms = dms
    return ns


@pytest.mark.asyncio
async def test_execute_kill_writes_db_first_then_cancels(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering invariant: kill_active=True is committed BEFORE cancel sweep.

    We instrument the broker's get_orders_open to inspect the DB at the
    moment of the fetch call — by then the kill_active column MUST be
    True (D-37 / PATTERNS §4 anti-pattern row 13).
    """
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    db_state_at_fetch: dict[str, Any] = {}

    async def _fetch(self_mock: Any = None) -> list[dict[str, Any]]:
        async with kill_seam.sf() as session:
            row = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).scalar_one()
            db_state_at_fetch["kill_active_at_fetch"] = bool(row.kill_active)
        return []

    broker = MagicMock(spec=Brokerage)
    broker.get_orders_open = AsyncMock(side_effect=_fetch)
    broker.cancel_order = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    assert db_state_at_fetch["kill_active_at_fetch"] is True, (
        "D-37 ordering invariant violated: kill_active was NOT True at "
        "the moment get_orders_open fired."
    )
    assert tally["total"] == 0


@pytest.mark.asyncio
async def test_execute_kill_emits_pair_of_audit_events(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ``kill_switch`` events: action='kill' (open) + action='kill_complete'."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _make_broker_with_open_orders(
        [
            {"id": "ord-1", "symbol": "NVDA"},
            {"id": "ord-2", "symbol": "AAPL"},
        ]
    )
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    async with kill_seam.sf() as session:
        events = (
            await session.execute(
                select(Event).order_by(Event.id.asc())
            )
        ).scalars().all()

    kill_events = [e for e in events if e.event_type == "kill_switch"]
    assert len(kill_events) == 2, (
        f"expected 2 kill_switch events, found {len(kill_events)}: "
        f"{[e.payload_json for e in kill_events]!r}"
    )
    assert '"action":"kill"' in kill_events[0].payload_json
    assert '"action":"kill_complete"' in kill_events[1].payload_json
    assert '"source":"slack"' in kill_events[0].payload_json
    assert '"reason":"manual"' in kill_events[0].payload_json
    # Tally landed in the close event.
    assert '"tally"' in kill_events[1].payload_json
    assert tally["cancelled"] == 2
    assert tally["total"] == 2


@pytest.mark.asyncio
async def test_execute_kill_cancels_in_parallel(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel calls run via asyncio.gather — wall-clock < sum of per-cancel delays.

    Each ``cancel_order`` takes 0.5s; with 3 orders sequential = 1.5s but
    parallel via asyncio.gather should be ~0.5s. We allow 1.0s headroom.
    """
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _make_broker_with_open_orders(
        [{"id": f"ord-{i}"} for i in range(3)],
        cancel_delay=0.5,
    )
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    import time as _time

    start = _time.monotonic()
    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )
    elapsed = _time.monotonic() - start

    assert tally["cancelled"] == 3
    assert elapsed < 1.0, (
        f"cancel sweep should run in parallel (3 × 0.5s parallel = ~0.5s); "
        f"elapsed {elapsed:.2f}s suggests sequential execution"
    )


@pytest.mark.asyncio
async def test_execute_kill_timeout_fallback_marks_pending(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cancel sweep exceeds 4s, remaining orders are reported as pending."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    # Per-cancel delay = 10s, gather timeout = 4s → all marked pending.
    broker = _make_broker_with_open_orders(
        [{"id": "ord-1"}],
        cancel_delay=10.0,
    )
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    # Patch wait_for timeout to a much smaller value just for this test so
    # the test itself doesn't sit on a 4s wall.  We patch the asyncio name
    # the module imported.
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro: Any, timeout: float) -> Any:
        return await real_wait_for(coro, timeout=0.2)

    monkeypatch.setattr(ks_mod.asyncio, "wait_for", _fast_wait_for)

    tally = await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    assert tally["pending"] == 1
    assert tally["cancelled"] == 0
    assert tally["total"] == 1


@pytest.mark.asyncio
async def test_execute_kill_sends_slack_dm_via_executor_seam(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DM routes through executor._send_slack_dm (identity-split safe)."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id)

    broker = _make_broker_with_open_orders([{"id": "ord-1"}])
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="manual"
    )

    assert len(kill_seam.dms) >= 1
    target_user, body = kill_seam.dms[-1]
    assert target_user == user_id
    assert "Kill ACTIVE" in body
    assert "Cancelled 1/1" in body


@pytest.mark.asyncio
async def test_execute_unkill_clears_column_and_writes_event(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_execute_unkill flips kill_active=False + writes action='unkill' event."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id, kill_active=True)

    await ks_mod._execute_unkill(user_id=user_id, source="slack")

    async with kill_seam.sf() as session:
        row = (
            await session.execute(
                select(User).where(User.user_id == user_id)
            )
        ).scalar_one()
        assert row.kill_active is False
        assert row.kill_active_since is None
        assert row.kill_active_reason is None

        events = (
            await session.execute(select(Event).order_by(Event.id.asc()))
        ).scalars().all()

    kill_events = [e for e in events if e.event_type == "kill_switch"]
    assert len(kill_events) == 1
    assert '"action":"unkill"' in kill_events[0].payload_json
    assert '"source":"slack"' in kill_events[0].payload_json


# ---------------------------------------------------------------------------
# is_active — reads DB fresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_active_reads_db_fresh(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``is_active`` returns the current DB value, NOT a cached one."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id, kill_active=False)

    assert await ks_mod.is_active(user_id) is False

    # Flip the column directly.
    async with kill_seam.sf() as session, session.begin():
        from sqlalchemy import update as _update

        await session.execute(
            _update(User).where(User.user_id == user_id).values(kill_active=True)
        )

    assert await ks_mod.is_active(user_id) is True


# ---------------------------------------------------------------------------
# Cross-restart persistence — DB column survives engine cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_state_persists_across_session_via_db_column(
    kill_seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_active persists in the DB column — no in-memory cache fallback."""
    from gekko.execution import kill_switch as ks_mod

    user_id = "test-user"
    await _seed_user(kill_seam.sf, user_id, kill_active=False)

    broker = _make_broker_with_open_orders([])
    monkeypatch.setattr(ks_mod, "_build_kill_broker", lambda _u: broker)

    await ks_mod._execute_kill(
        user_id=user_id, source="slack", reason="restart-persistence-test"
    )

    # Pretend the process restarted — re-read the column.
    async with kill_seam.sf() as session:
        row = (
            await session.execute(
                select(User).where(User.user_id == user_id)
            )
        ).scalar_one()
        assert row.kill_active is True
        assert row.kill_active_reason == "restart-persistence-test"
