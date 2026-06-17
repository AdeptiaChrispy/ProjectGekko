"""Kill-switch WRITE orchestrator — Plan 02-05 Task 1 (D-35 / D-36 / D-37 / EXEC-06).

The WRITE side of the kill switch. Plan 02-02's :mod:`gekko.execution.checks._kill_switch`
ships the READ side (OrderGuard refuses new place_order calls when ``users.kill_active``
is True). This module ships the WRITE side: the orchestrator that flips the
column, cancels open orders in parallel with a 4s timeout, and DMs the
operator with the tally.

**Ordering invariant (D-37 / PATTERNS §4 anti-pattern row 13):**

  1. SET ``users.kill_active=True`` + write a ``kill_switch`` audit event
     (``action="kill"``, ``ts_start=...``). The DB COMMIT happens FIRST so
     that the OrderGuard READ side refuses new orders the moment the row
     visible-commits — even if the parallel cancel sweep is still in flight.
  2. Fetch open orders from the broker(s).
  3. Run ``asyncio.wait_for(asyncio.gather(*cancels), timeout=4.0)`` — best-
     effort parallel cancel; failures + timeouts surface in the tally.
  4. SECOND DB transaction writes a ``kill_switch`` event
     (``action="kill_complete"``, ``ts_end=...``, ``tally={cancelled, pending,
     failed, total}``).
  5. Slack DM operator with the tally summary — DM is OUTSIDE both audit
     transactions per PATTERNS §4 anti-pattern row 14 ("do work + audit + DM
     outside transaction").

**5-second SLA (RESEARCH §3 hop analysis):**

  * Hop 1: Slack/CLI/dashboard → ``_execute_kill`` entry (immediate)
  * Hop 2: DB write to set ``kill_active=True`` (~1s SQLCipher worst case)
  * Hop 3: ``asyncio.gather`` parallel cancel-all with 4s ``wait_for`` timeout
  * Hop 4: Second audit event + Slack DM (~0.5s)

  Total budget = ~5.5s worst case; ~420ms typical. The 4s timeout is the
  load-bearing knob — failures bubble up as "failed" / "pending" in the
  tally, but the kill_active column was already flipped in step 1 so no
  new orders will fire even if the cancel sweep times out.

**Persistence model (D-36):**

  * Source of truth is ``users.kill_active`` in the per-user SQLCipher DB.
  * NEVER an in-memory module-global cache (PATTERNS §4 anti-pattern row 6).
  * FastAPI lifespan SELECTs the column at startup; if True, DMs the
     operator + sets ``app.state.kill_active=True`` (plan 02-05 Task 3).
  * ``_execute_unkill`` clears the column; new orders flow again.

**Test seam (PATTERNS §3c):**

  Module-local ``_get_session_factory(user_id)`` — verbatim copy of the
  executor + check_kill_switch seam. Tests monkeypatch this to point at
  an in-memory engine. The ``finally: if engine is not None: await
  engine.dispose()`` pattern is the disposal contract.

  ``_send_slack_dm`` is reused from :mod:`gekko.execution.executor` so the
  identity-split fix (quick task 260612-nlv) flows through every DM path.
  PATTERNS §10 RESEARCH note explicitly forbids a parallel DM path.

The Claude Agent SDK MUST NOT be imported here. AST gate
(``tests/unit/test_kill_switch.py``) enforces it.

References:
  * CONTEXT.md D-35 / D-36 / D-37 (kill switch persistence + ordering)
  * RESEARCH §3 (kill switch persistence model + 5s SLA hop analysis)
  * PATTERNS §1a row 12 (kill_switch.py — vault.passphrase + executor patterns)
  * PATTERNS §3c (session-factory + finally-dispose)
  * PATTERNS §3d (audit event with normalize_decimals)
  * PATTERNS §4 anti-pattern row 13 (ordering invariant)
  * PATTERNS §4 anti-pattern row 14 (DM outside transaction)
  * PATTERNS §10 (identity-split — single _send_slack_dm seam)
  * tests/unit/test_alpaca_retry.py — AST gate verifies cancel_all_open_orders
    has zero retry decorators
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import User
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

if TYPE_CHECKING:  # pragma: no cover
    from gekko.brokers.base import Brokerage

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level seams (PATTERNS §3c)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors :func:`gekko.execution.executor._get_session_factory` exactly so
    tests have a per-module monkeypatch seam — patching the executor's seam
    does NOT also patch this one (each layer owns its own engine lifecycle).
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


def _build_kill_broker(user_id: str) -> Brokerage:
    """Construct the broker(s) used by the kill sweep.

    Per RESEARCH §3 line 686: for plan 02-05's PAPER-only kill demo, this
    constructs the paper broker. Plan 02-06 extends to also iterate the
    live-credential row when present and return a list of brokers — for
    now the kill sweep operates on the single paper broker.

    Tests monkeypatch this to return a MagicMock so no live broker is
    constructed at unit-test time.
    """
    # Late import to keep kill_switch.py importable in environments that
    # don't have alpaca-py wired (CLI bootstrap path).
    from gekko.brokers.alpaca import AlpacaBroker

    settings = get_settings()
    return AlpacaBroker(
        api_key=settings.alpaca_paper_api_key.get_secret_value(),
        secret_key=settings.alpaca_paper_secret_key.get_secret_value(),
        paper=True,
    )


# ---------------------------------------------------------------------------
# _execute_kill — the DB-first orchestrator (D-37 ordering invariant)
# ---------------------------------------------------------------------------


async def _execute_kill(
    *,
    user_id: str,
    source: str,
    reason: str = "manual",
) -> dict[str, Any]:
    """Flip ``users.kill_active=True`` then parallel-cancel open orders.

    Ordering invariant (D-37 / PATTERNS §4 anti-pattern row 13): the DB
    write FIRST commits ``kill_active=True``; only AFTER that commit does
    the cancel sweep run. New ``place_order`` calls hitting OrderGuard
    after step 1 will be rejected with ``reject_code="kill_active"`` even
    while the sweep is still in flight.

    :param user_id: The per-user SQLCipher DB scope.
    :param source: ``"slack"`` / ``"cli"`` / ``"dashboard"`` — the surface
        that issued the kill. Persisted in the audit payload for forensic
        attribution.
    :param reason: Optional operator-supplied reason string. Defaults to
        ``"manual"``. Persisted in the audit payload.
    :returns: Tally dict ``{"cancelled": X, "pending": Y, "failed": Z,
        "total": N, "ts_start": "...", "ts_end": "..."}``.
    """
    sf, engine = _get_session_factory(user_id)
    ts_start = datetime.now(UTC).isoformat()
    log.info(
        "kill_switch.activate.begin",
        user_id=user_id,
        source=source,
        reason=reason,
        ts_start=ts_start,
    )
    try:
        # ---- STEP 1: flip kill_active=True FIRST + write the "open" event.
        # The commit on this transaction is the load-bearing moment — new
        # place_order calls hitting OrderGuard after this point will see
        # kill_active=True and refuse.
        async with sf() as session, session.begin():
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    kill_active=True,
                    kill_active_since=ts_start,
                    kill_active_reason=reason,
                )
            )
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,  # global event — see audit/log.py docstring
                event_type="kill_switch",
                payload=normalize_decimals(
                    {
                        "action": "kill",
                        "source": source,
                        "reason": reason,
                        "ts_start": ts_start,
                    }
                ),
            )

        # ---- STEP 2: fetch open orders, then parallel cancel with 4s timeout.
        # BL-02 fix: the entire sweep is wrapped in try/except + try/finally
        # so the ``kill_complete`` audit event ALWAYS lands on the chain,
        # even when the cancel sweep raises (asyncio cancellation, broker
        # ConnectionError, sqlalchemy operational error against the second
        # session, etc.). The previous shape lost the tally + ts_end when
        # an uncaught exception escaped between the "kill" event and the
        # "kill_complete" event — the kill switch's forensic story is
        # load-bearing and must never silently disappear.
        tally: dict[str, Any] = {
            "cancelled": 0,
            "pending": 0,
            "failed": 0,
            "total": 0,
        }
        ts_end: str | None = None
        try:
            broker = _build_kill_broker(user_id)
            try:
                open_orders = await broker.get_orders_open()
                tally["total"] = len(open_orders)
            except Exception as e:  # noqa: BLE001 — fetch failure surfaces in tally
                log.exception(
                    "kill_switch.fetch_open_orders_failed",
                    user_id=user_id,
                )
                tally["error"] = f"fetch_open: {e!s}"
                open_orders = []

            if open_orders:
                # PATTERNS §5d background-task shape: each cancel is wrapped so
                # one exception doesn't abort the whole gather.
                async def _cancel_one(order_id: str) -> tuple[str, bool, str]:
                    try:
                        ok = await broker.cancel_order(order_id)
                        return (order_id, bool(ok), "")
                    except Exception as exc:  # noqa: BLE001
                        return (order_id, False, str(exc))

                cancel_coros = [_cancel_one(str(o.get("id", ""))) for o in open_orders]
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*cancel_coros, return_exceptions=False),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    # In-flight cancels keep running but we report them as
                    # "pending" — the kill_active column was already flipped,
                    # so no new orders will fire while the sweep finishes.
                    log.warning(
                        "kill_switch.cancel_timeout",
                        user_id=user_id,
                        total=tally["total"],
                    )
                    tally["pending"] = tally["total"]
                else:
                    for _oid, ok, _err in results:
                        if ok:
                            tally["cancelled"] += 1
                        else:
                            tally["failed"] += 1
        except Exception as e:  # noqa: BLE001 — sweep-level failure
            log.exception(
                "kill_switch.sweep_failed",
                user_id=user_id,
            )
            tally.setdefault("error", f"sweep: {e!s}")
        finally:
            # BL-02 fix: the ``kill_complete`` audit event is the canonical
            # forensic record of the sweep tally + ts_end. It MUST emit
            # regardless of how the sweep terminated. The Slack DM is also
            # best-effort here so a DM failure doesn't suppress the
            # post-finally bookkeeping.
            ts_end = datetime.now(UTC).isoformat()
            tally["ts_start"] = ts_start
            tally["ts_end"] = ts_end

            # ---- STEP 3: write the "kill_complete" audit event.
            try:
                async with sf() as session, session.begin():
                    await append_event(
                        session,
                        user_id=user_id,
                        strategy_id=None,
                        event_type="kill_switch",
                        payload=normalize_decimals(
                            {
                                "action": "kill_complete",
                                "source": source,
                                "reason": reason,
                                "ts_start": ts_start,
                                "ts_end": ts_end,
                                "tally": dict(tally),
                            }
                        ),
                    )
            except Exception:  # noqa: BLE001
                # If even the audit write fails, log loudly. The kill_active
                # column was still flipped in step 1, so the safety floor
                # holds even when the audit chain entry is lost.
                log.exception(
                    "kill_switch.complete_audit_failed",
                    user_id=user_id,
                    tally=dict(tally),
                )

            # ---- STEP 4: Slack DM (OUTSIDE the audit transaction — PATTERNS §4 row 14).
            # ``_dm_kill_summary`` already wraps the DM in try/except, but
            # be defensive at the call site too so a future refactor of the
            # helper doesn't reopen the event-loss window.
            try:
                await _dm_kill_summary(user_id, tally)
            except Exception:  # noqa: BLE001
                log.exception(
                    "kill_switch.dm_outside_failed",
                    user_id=user_id,
                )

        log.info(
            "kill_switch.activate.complete",
            user_id=user_id,
            source=source,
            tally=dict(tally),
        )
        return tally
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# _execute_unkill — clear kill_active + audit + DM
# ---------------------------------------------------------------------------


async def _execute_unkill(
    *,
    user_id: str,
    source: str,
) -> None:
    """Clear ``users.kill_active`` and write a ``kill_switch`` ``unkill`` event.

    Per UI-SPEC §2b unkill modal copy + RESEARCH §3: previously-cancelled
    orders are NOT restored — unkill only flips the column back so new
    place_order calls flow again.

    :param user_id: The per-user SQLCipher DB scope.
    :param source: ``"slack"`` / ``"cli"`` / ``"dashboard"``.
    """
    sf, engine = _get_session_factory(user_id)
    ts = datetime.now(UTC).isoformat()
    log.info(
        "kill_switch.deactivate.begin",
        user_id=user_id,
        source=source,
        ts=ts,
    )
    try:
        async with sf() as session, session.begin():
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    kill_active=False,
                    kill_active_since=None,
                    kill_active_reason=None,
                )
            )
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="kill_switch",
                payload=normalize_decimals(
                    {
                        "action": "unkill",
                        "source": source,
                        "ts": ts,
                    }
                ),
            )

        # DM OUTSIDE the transaction (PATTERNS §4 row 14).
        # BL-02 fix: wrap the DM in try/except so a Slack outage during
        # unkill (or any _send_slack_dm failure) does NOT raise out of
        # _execute_unkill after the DB commit. The unkill DB transaction
        # has already committed at this point — letting the DM exception
        # propagate would leave the caller's outer try/except to swallow
        # it with no audit trace and an inconsistent operator UX
        # ("did unkill succeed?"). The kill_active column is the source
        # of truth; the DM is a notification, not a state transition.
        from gekko.execution.executor import _send_slack_dm

        try:
            await _send_slack_dm(
                user_id,
                "✅ Kill cleared — new orders will fire again. "
                "Note: previously-cancelled orders were NOT restored.",
            )
        except Exception:  # noqa: BLE001 — DM failure must not abort unkill
            log.exception(
                "kill_switch.unkill_dm_failed",
                user_id=user_id,
                source=source,
            )
        log.info("kill_switch.deactivate.complete", user_id=user_id, source=source)
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# is_active — convenience helper for the boot-time lifespan check
# ---------------------------------------------------------------------------


async def is_active(user_id: str) -> bool:
    """Return True iff ``users.kill_active`` is True for ``user_id``.

    Reads the DB FRESH every call — no in-memory cache (PATTERNS §4 anti-
    pattern row 6). The dashboard layer wraps this with a 60s TTL on
    ``app.state`` per UI-SPEC §"Trigger logic" — that cache lives in the
    dashboard module, NOT here.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).scalar_one_or_none()
            return bool(row is not None and row.kill_active)
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Slack DM helper — routes through the executor seam (PATTERNS §10)
# ---------------------------------------------------------------------------


async def _dm_kill_summary(user_id: str, tally: dict[str, Any]) -> None:
    """Send the post-kill summary DM via the executor's ``_send_slack_dm`` seam.

    The body is a deterministic constant string per UI-SPEC §"Slack Block
    Kit Parallels Summary" — but it still routes through ``_send_slack_dm``
    so the Phase-1 identity-split fix (quick task 260612-nlv) applies.
    """
    from gekko.execution.executor import _send_slack_dm

    cancelled = tally.get("cancelled", 0)
    pending = tally.get("pending", 0)
    failed = tally.get("failed", 0)
    total = tally.get("total", 0)
    text = (
        f"🚫 Kill ACTIVE. Cancelled {cancelled}/{total}. "
        f"{pending} pending. {failed} failed (see logs)."
    )
    try:
        await _send_slack_dm(user_id, text)
    except Exception:  # noqa: BLE001 — DM failure must not abort the kill
        log.exception(
            "kill_switch.dm_failed",
            user_id=user_id,
            tally=dict(tally),
        )


__all__: tuple[str, ...] = (
    "_execute_kill",
    "_execute_unkill",
    "is_active",
)
