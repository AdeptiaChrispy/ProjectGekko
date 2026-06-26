"""Single-day-drawdown anomaly evaluator — Plan 05-04 Task 1 (TRUST-04 / SC-4).

``evaluate_drawdown`` is the reflex: given a strategy that is
``auto-within-caps``, compute its single-day drawdown against a STABLE
start-of-day value snapshot (the operator-confirmed denominator, OQ#3) and,
if the drawdown breaches the per-strategy ``anomaly_threshold_pct`` (default
10%), demote it to ``propose-only``, cancel its pending auto-orders, and fire
an urgent quiet-hours-bypassing Slack DM.

Design invariants (all enforced by ``tests/unit/test_anomaly.py``):

  * **Idempotent.** A strategy not in ``auto-within-caps`` is a no-op — the
    drawdown is not even computed (mirror ``stamp_first_live_trade`` set-once).
  * **Earlier rung than the hard cap (D-T11).** The anomaly threshold (a % of
    start-of-day value) trips at a smaller loss than the per-strategy
    ``max_daily_loss_usd`` hard cap, so autonomy is revoked before trading is
    halted. The evaluator NEVER touches a kill switch or halts trading — the
    strategy keeps running research after demotion (D-T12).
  * **Surgical (D-T12).** Demotion + cancellation are scoped to the named
    strategy only; no cascade.
  * **Decimal-exact.** All drawdown math is :class:`decimal.Decimal`; no float.

The reflex calls into ``strategy.trust.demote_strategy_from_auto`` — the
AST-gated SOLE writer of ``trust_level`` — it never writes ``trust_level``
directly. Cancellation reuses ``OrderGuard.get_orders_open`` + ``cancel_order``
passthroughs (broker side) and the ``proposals`` PENDING→REJECTED edge
(auto-proposal side), carrying an ``anomaly_demotion`` reason (OQ#5 — no new
state).

No ``claude_agent_sdk`` import — this module sits on the trust/cancellation
path; LLM bytes never reach it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Event
from gekko.db.models import Proposal as ProposalRow
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.strategy.promotion import load_strategy_metadata
from gekko.strategy.trust import (
    TRUST_AUTO,
    demote_strategy_from_auto,
)
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: Per-strategy single-day-drawdown threshold default (10%) when the
#: ``anomaly_threshold_pct`` column is NULL (D-T11). Mirrors the migration
#: 0007 server_default.
DEFAULT_ANOMALY_THRESHOLD_PCT = "0.10"

#: Discriminator key stamped on the start-of-day snapshot event so the
#: evaluator can read its STABLE denominator back without a new event type.
#: The snapshot rides on the existing ``daily_pnl`` event type (the daily
#: digest aggregator only reacts to fill/error/cap_rejection events, so a
#: discriminated daily_pnl row is invisible to it — no migration needed).
SOD_SNAPSHOT_KIND = "sod_snapshot"


# ---------------------------------------------------------------------------
# Module-level session-factory shim (verbatim from promotion.py / _hard_caps.py)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``."""
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


def _today_utc_window() -> tuple[str, str]:
    """Return the (start_iso, end_iso) ISO-8601 strings bracketing today UTC."""
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


# ---------------------------------------------------------------------------
# Start-of-day snapshot (the STABLE drawdown denominator — OQ#3)
# ---------------------------------------------------------------------------


async def snapshot_start_of_day_value(
    *, user_id: str, strategy_name: str, broker: Any
) -> Decimal:
    """Persist this strategy's start-of-day book value (the drawdown denominator).

    Called once per trading day near NYSE open by the scheduler's market-open
    job (Plan 05-04 Task 3). Computes Σ market_value of the strategy's held
    positions (the current value at open establishes the STABLE denominator all
    intraday evaluations divide by) and writes it as a discriminated
    ``daily_pnl`` snapshot event. Reading live equity each tick would give a
    moving denominator that makes the drawdown % oscillate (RESEARCH Pitfall 3);
    a persisted snapshot pins it.

    Returns the snapshotted value (also for the caller's logging).
    """
    sod_value = await _compute_current_value(user_id, strategy_name, broker)
    now_iso = datetime.now(UTC).isoformat()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="daily_pnl",
                payload=normalize_decimals(
                    {
                        "kind": SOD_SNAPSHOT_KIND,
                        "strategy_name": strategy_name,
                        "start_of_day_value_usd": str(sod_value),
                        "snapshotted_at": now_iso,
                    }
                ),
            )
        log.info(
            "anomaly.sod_snapshot_written",
            user_id=user_id,
            strategy_name=strategy_name,
            start_of_day_value_usd=str(sod_value),
        )
    finally:
        if engine is not None:
            await engine.dispose()
    return sod_value


# Backwards-compatible private alias used in the plan's artifact list.
_snapshot_start_of_day_value = snapshot_start_of_day_value


async def _load_start_of_day_value(
    user_id: str, strategy_name: str
) -> Decimal:
    """Read back today's most-recent persisted start-of-day snapshot.

    Returns ``Decimal('0')`` when no snapshot has been written today (the
    market-open job has not yet run, or the strategy was promoted intraday) —
    the caller treats a zero denominator as "no trip" so the reflex stays a
    no-op until the snapshot exists.
    """
    start_iso, end_iso = _today_utc_window()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event)
                    .where(
                        Event.user_id == user_id,
                        Event.event_type == "daily_pnl",
                        Event.ts >= start_iso,
                        Event.ts <= end_iso,
                    )
                    .order_by(Event.id.desc())
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    for row in rows:
        try:
            outer = json.loads(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        payload = outer.get("payload", outer)
        if payload.get("kind") != SOD_SNAPSHOT_KIND:
            continue
        if payload.get("strategy_name") != strategy_name:
            continue
        raw = payload.get("start_of_day_value_usd")
        if raw is None:
            continue
        try:
            return Decimal(str(raw))
        except Exception:  # noqa: BLE001 - skip malformed
            continue
    return Decimal("0")


# ---------------------------------------------------------------------------
# Current value + drawdown math (Decimal-exact)
# ---------------------------------------------------------------------------


async def _compute_current_value(
    user_id: str, strategy_name: str, broker: Any
) -> Decimal:
    """Σ market_value of this strategy's held positions + today's realized P&L.

    Positions are attributed to the strategy by watchlist membership (Alpaca
    nets one position per ticker — the same pragmatic per-strategy attribution
    used by ``check_capital_ceiling``). When the watchlist is unavailable the
    full position book is summed (degrades to an account-level measure rather
    than a false zero). Today's realized P&L (from the fill-event scan, signed)
    is added so a sudden realized loss moves the current value down immediately.
    """
    # Resolve this strategy's watchlist tickers (best-effort).
    watchlist = await _load_watchlist(user_id, strategy_name)

    positions: list[dict[str, Any]] = []
    try:
        positions = await broker.get_positions()
    except Exception:  # noqa: BLE001 - best-effort; treat as empty book
        positions = []

    market_value = Decimal("0")
    for pos in positions:
        sym = str(pos.get("symbol") or pos.get("asset_id") or "")
        if watchlist and sym not in watchlist:
            continue
        mv_raw = pos.get("market_value")
        if mv_raw is None:
            mv_raw = pos.get("cost_basis")
        if mv_raw is None:
            continue
        try:
            market_value += Decimal(str(mv_raw))
        except Exception:  # noqa: BLE001 - skip malformed
            continue

    realized = await _today_realized_pnl(user_id, strategy_name)
    return market_value + realized


async def _load_watchlist(user_id: str, strategy_name: str) -> set[str]:
    """Best-effort watchlist lookup for the latest version of ``strategy_name``."""
    from gekko.db.models import Strategy as StrategyRow
    from gekko.schemas.strategy import Strategy

    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(StrategyRow)
                    .where(
                        StrategyRow.user_id == user_id,
                        StrategyRow.strategy_name == strategy_name,
                    )
                    .order_by(StrategyRow.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    finally:
        if engine is not None:
            await engine.dispose()
    if row is None or not row.payload_json:
        return set()
    try:
        return set(Strategy.model_validate_json(row.payload_json).watchlist)
    except Exception:  # noqa: BLE001 - degrade to account-level
        return set()


async def _today_realized_pnl(user_id: str, strategy_name: str) -> Decimal:
    """Signed realized P&L today for this strategy (from the fill-event scan).

    Mirrors the daily-P&L sign convention: a ``realized_pnl_usd`` key wins when
    present; otherwise BUY = cash out (negative), SELL = cash in (positive).
    Scoped to ``strategy_name`` so the measure is surgical.
    """
    start_iso, end_iso = _today_utc_window()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event)
                    .where(
                        Event.user_id == user_id,
                        Event.event_type == "fill",
                        Event.ts >= start_iso,
                        Event.ts <= end_iso,
                    )
                    .limit(1000)
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    realized = Decimal("0")
    for row in rows:
        try:
            outer = json.loads(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        payload = outer.get("payload", outer)
        if payload.get("strategy_name") != strategy_name:
            continue
        if "realized_pnl_usd" in payload:
            try:
                realized += Decimal(str(payload["realized_pnl_usd"]))
            except Exception:  # noqa: BLE001 - skip malformed
                continue
            continue
        try:
            qty = Decimal(str(payload.get("filled_qty", "0")))
            price = Decimal(str(payload.get("filled_avg_price", "0")))
            side = str(payload.get("side", "buy")).lower()
            realized += (price * qty) if side == "sell" else -(price * qty)
        except Exception:  # noqa: BLE001 - skip malformed
            continue
    return realized


async def _compute_single_day_drawdown_pct(
    user_id: str, strategy_name: str, broker: Any
) -> Decimal:
    """Decimal-exact ``(start_of_day - current) / start_of_day`` drawdown %.

    Reads the STABLE persisted start-of-day snapshot (the denominator, OQ#3)
    and the current value. Guards a non-positive denominator → ``Decimal('0')``
    (no divide-by-zero, no false trip before the market-open snapshot has run).
    A negative drawdown (book is UP) is clamped to ``Decimal('0')`` — the reflex
    only fires on losses.
    """
    sod = await _load_start_of_day_value(user_id, strategy_name)
    if sod <= Decimal("0"):
        return Decimal("0")
    current = await _compute_current_value(user_id, strategy_name, broker)
    dd = (sod - current) / sod
    if dd < Decimal("0"):
        return Decimal("0")
    return dd


# ---------------------------------------------------------------------------
# Cancellation of pending auto-orders (broker open orders + PENDING proposals)
# ---------------------------------------------------------------------------


async def _cancel_pending_auto_orders(
    user_id: str, strategy_name: str, broker: Any
) -> int:
    """Cancel this strategy's pending auto-orders. Returns the cancelled count.

    Two surfaces, both scoped to ``strategy_name`` (surgical, D-T12 / T-05-16):

      1. **Open broker orders** — via ``broker.get_orders_open()`` +
         ``broker.cancel_order(broker_order_id)`` passthroughs. Only orders
         whose ticker is in this strategy's watchlist are cancelled (Alpaca
         orders carry no strategy tag; watchlist membership is the attribution).
      2. **PENDING auto-proposals** — transitioned PENDING→REJECTED via
         :func:`reject_pending_auto_proposals_for_anomaly`, carrying an
         ``anomaly_demotion`` reason (OQ#5 — reuse the existing edge, no new
         state).

    Best-effort: a broker cancel failure is logged and skipped so a single
    stuck order never blocks the demotion.
    """
    watchlist = await _load_watchlist(user_id, strategy_name)
    cancelled = 0

    # 1. Broker open orders.
    try:
        open_orders = await broker.get_orders_open()
    except Exception:  # noqa: BLE001 - best-effort
        log.exception(
            "anomaly.get_orders_open_failed",
            user_id=user_id,
            strategy_name=strategy_name,
        )
        open_orders = []

    for order in open_orders:
        sym = str(order.get("symbol") or order.get("ticker") or "")
        if watchlist and sym not in watchlist:
            continue
        broker_order_id = str(
            order.get("broker_order_id") or order.get("id") or ""
        )
        if not broker_order_id:
            continue
        try:
            ok = await broker.cancel_order(broker_order_id)
            if ok:
                cancelled += 1
        except Exception:  # noqa: BLE001 - best-effort
            log.exception(
                "anomaly.cancel_order_failed",
                user_id=user_id,
                strategy_name=strategy_name,
                broker_order_id=broker_order_id,
            )

    # 2. PENDING auto-proposals.
    cancelled += await reject_pending_auto_proposals_for_anomaly(
        user_id=user_id, strategy_name=strategy_name
    )
    return cancelled


async def reject_pending_auto_proposals_for_anomaly(
    *, user_id: str, strategy_name: str
) -> int:
    """Transition this strategy's PENDING auto-proposals PENDING→REJECTED.

    The proposals.py helper records the reason (``anomaly_demotion``) so the
    audit trail distinguishes an anomaly cancellation from an operator reject
    (OQ#5 — reuse the existing edge; no new state). Returns the count.
    """
    from gekko.approval.proposals import reject_proposal
    from gekko.db.models import Strategy as StrategyRow

    sf, engine = _get_session_factory(user_id)
    rejected = 0
    try:
        async with sf() as session, session.begin():
            # Resolve this strategy's snapshot ids (any version) so we only
            # touch proposals authored against THIS strategy (surgical).
            strat_ids = (
                await session.execute(
                    select(StrategyRow.strategy_id).where(
                        StrategyRow.user_id == user_id,
                        StrategyRow.strategy_name == strategy_name,
                    )
                )
            ).scalars().all()
            if not strat_ids:
                return 0
            pending = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.user_id == user_id,
                        ProposalRow.status == "PENDING",
                        ProposalRow.strategy_id.in_(list(strat_ids)),
                    )
                )
            ).scalars().all()
            for row in pending:
                await reject_proposal(
                    session,
                    row.proposal_id,
                    actor="anomaly-demotion",
                    reason="anomaly_demotion",
                )
                rejected += 1
    finally:
        if engine is not None:
            await engine.dispose()
    return rejected


# ---------------------------------------------------------------------------
# Urgent Slack DM (bypasses quiet hours — D-T13)
# ---------------------------------------------------------------------------


async def _send_anomaly_dm(
    user_id: str,
    strategy_name: str,
    drawdown_pct: Decimal,
    threshold: Decimal,
    cancelled_count: int,
) -> None:
    """Fire the urgent anomaly-demotion DM (bypasses quiet hours, D-T13).

    Routes through ``executor._send_slack_dm_respecting_quiet_hours`` with the
    ``"anomaly_demotion"`` category (added to ``_BYPASS_CATEGORIES`` in Task 2),
    so the operator is informed regardless of their quiet window — same
    operator-safety tier as kill-switch / cap-rejection / first-live (D-T13).
    Decimal-exact percent formatting; URL deep-link only (no approve/reject).
    """
    from gekko.execution.executor import (
        _send_slack_dm_respecting_quiet_hours,
    )
    from gekko.reporter.slack import _get_dashboard_url

    dd_display = drawdown_pct * Decimal("100")
    thr_display = threshold * Decimal("100")
    url = f"{_get_dashboard_url()}/strategies"
    text = (
        f":rotating_light: *Anomaly demotion* — `{strategy_name}` auto-demoted "
        f"to *propose-only*.\n"
        f"Single-day drawdown {dd_display:.2f}% reached the "
        f"{thr_display:.2f}% threshold. "
        f"{cancelled_count} pending auto-order(s) cancelled. "
        f"Research keeps running; re-promote when the streak rebuilds.\n"
        f"<{url}|Open strategies dashboard>"
    )
    # bypass-category: anomaly_demotion — operator-safety-critical; must reach
    # the operator regardless of quiet hours (D-T13).
    await _send_slack_dm_respecting_quiet_hours(
        user_id, text, category="anomaly_demotion"
    )


# ---------------------------------------------------------------------------
# The reflex
# ---------------------------------------------------------------------------


async def evaluate_drawdown(
    *, user_id: str, strategy_name: str, broker: Any
) -> bool:
    """Evaluate single-day drawdown; demote + cancel + DM on breach.

    :returns: ``True`` if the strategy was demoted by this call, else ``False``.

    Idempotent (a non-auto strategy is a no-op without computing drawdown),
    surgical (only ``strategy_name`` is touched), and earlier than the hard
    ``max_daily_loss_usd`` cap (it removes autonomy without halting trading).
    """
    md = await load_strategy_metadata(
        user_id=user_id, strategy_name=strategy_name
    )
    if md is None or md.trust_level != TRUST_AUTO:
        # Idempotent no-op — already propose-only (or no metadata row). Do not
        # even compute the drawdown (mirror the set-once stamp short-circuit).
        return False

    dd = await _compute_single_day_drawdown_pct(user_id, strategy_name, broker)
    threshold = Decimal(
        str(md.anomaly_threshold_pct or DEFAULT_ANOMALY_THRESHOLD_PCT)
    )
    if dd < threshold:
        return False

    # --- Breach: surgical demote + cancel + urgent DM. ---
    cancelled_count = await _cancel_pending_auto_orders(
        user_id, strategy_name, broker
    )
    await demote_strategy_from_auto(
        user_id=user_id,
        strategy_name=strategy_name,
        reason="anomaly",
        drawdown_pct=str(dd),
    )
    # Write the dedicated anomaly_demotion audit event (distinct from the
    # trust_demoted event the demote helper writes) so the digest + in-app
    # notice can read the drawdown %, threshold, and cancelled count.
    await _write_anomaly_demotion_event(
        user_id=user_id,
        strategy_name=strategy_name,
        drawdown_pct=dd,
        threshold=threshold,
        cancelled_count=cancelled_count,
    )
    try:
        await _send_anomaly_dm(
            user_id, strategy_name, dd, threshold, cancelled_count
        )
    except Exception:  # noqa: BLE001 - DM failure must not undo the demotion
        log.exception(
            "anomaly.dm_failed",
            user_id=user_id,
            strategy_name=strategy_name,
        )

    log.warning(
        "anomaly.strategy_demoted",
        user_id=user_id,
        strategy_name=strategy_name,
        drawdown_pct=str(dd),
        threshold=str(threshold),
        cancelled_count=cancelled_count,
    )
    return True


async def _write_anomaly_demotion_event(
    *,
    user_id: str,
    strategy_name: str,
    drawdown_pct: Decimal,
    threshold: Decimal,
    cancelled_count: int,
) -> None:
    """Append the first-class ``anomaly_demotion`` audit event (D-T12).

    Keyed on ``strategy_name`` in the payload with ``strategy_id=None`` (mirror
    the trust events). The in-app red notice (Task 2) + the daily digest read
    this event for today to render the drawdown %, threshold, and cancelled
    count.
    """
    now_iso = datetime.now(UTC).isoformat()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="anomaly_demotion",
                payload=normalize_decimals(
                    {
                        "strategy_name": strategy_name,
                        "drawdown_pct": str(drawdown_pct),
                        "threshold_pct": str(threshold),
                        "cancelled_count": cancelled_count,
                        "demoted_at": now_iso,
                    }
                ),
            )
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = (
    "DEFAULT_ANOMALY_THRESHOLD_PCT",
    "SOD_SNAPSHOT_KIND",
    "evaluate_drawdown",
    "reject_pending_auto_proposals_for_anomaly",
    "snapshot_start_of_day_value",
)
