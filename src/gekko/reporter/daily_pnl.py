"""Daily P&L digest DM — Plan 03-06 Task 1 (REPT-01).

Sends a Block Kit Slack DM at 16:30 America/New_York every trading day
summarising today's gross P&L, per-strategy breakdown, open positions count,
fills count, and errors count per UI-SPEC §Surface 6.

**D-59 NYSE schedule gate:** at function entry, the handler checks
``pandas_market_calendars`` for today's NYSE schedule. If the schedule is
empty (weekend, market holiday), the function returns ``False`` without
sending any DM — the operator's inbox stays clean.

**D-48 quiet-hours semantics:** ``daily_pnl`` is a ROUTINE category per D-48
(the Suppressed list explicitly enumerates "Daily P&L summary"). The cron
trigger fires at 16:30 ET on every trading day; the DM goes through
:func:`_send_slack_dm_respecting_quiet_hours` which defers it when 16:30 ET
falls within the user's quiet window. For the typical operator with US quiet
hours 22:00–07:00, 16:30 ET is comfortably outside the window and the DM
lands immediately.

**AST gate:** this module MUST NOT import ``claude_agent_sdk`` or
``anthropic``. It is a deterministic Python firewall — the LLM-authored
rationale bytes never reach this layer (validated by
``test_no_claude_sdk_in_p3_modules.py``).

References:
  * UI-SPEC §Surface 6 — Block Kit shape + copy contract
  * CONTEXT.md D-48 — quiet-hours category classification
  * CONTEXT.md D-59 — NYSE schedule gate
  * PATTERNS §2d — _get_session_factory shim
  * PATTERNS §2e — identity-split-safe DM seam
  * PATTERNS §2f — APScheduler string-ref pattern
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas_market_calendars as mcal
import structlog
from sqlalchemy import select
from zoneinfo import ZoneInfo

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Event, Proposal as ProposalRow
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.vault.passphrase import get_passphrase as _get_passphrase

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

log = structlog.get_logger(__name__)

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Module-level seam (PATTERNS §2d)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, "AsyncEngine | None"]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the identical shim in ``executor.py``, ``expiry.py``, and
    ``quiet_hours.py`` so tests have a per-module monkeypatch seam.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass
class DigestData:
    """Aggregated metrics for one calendar day per one user."""

    fills_count: int
    gross_pnl_usd: Decimal
    per_strategy: dict[str, dict[str, Any]]  # {strategy_name: {pnl_usd, fills_count}}
    errors_count: int
    cap_rejections_count: int
    open_positions_count: int
    # Plan 05-05 Task 3 (SC-2 / D-T18): auto-execution review surface.
    #: Total ``auto_execution`` events today (aggregate count).
    auto_exec_count: int = 0
    #: Set of strategy_names that auto-executed at least once today. Used to
    #: annotate per-strategy breakdown lines with 🤖 and to render the
    #: aggregate "across {M} strategies" count.
    auto_exec_strategies: set[str] = field(default_factory=set)
    # Plan 05-05 Task 3 (SC-2 / TRUST-04): anomaly-demotion summary.
    #: List of {"strategy_name", "drawdown_pct"} for today's anomaly demotions
    #: (latest-wins per strategy), rendered as the digest anomaly summary line.
    anomaly_demotions: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------


async def _aggregate_today_events(
    session: "AsyncSession",
    user_id: str,
    today_et: date,
) -> DigestData:
    """SELECT today's Event rows and aggregate into :class:`DigestData`.

    The date range is defined in ET (America/New_York) and converted to UTC
    for the WHERE clause: [today_et 00:00 ET, today_et+1 00:00 ET).

    Fill P&L uses the sign convention:
      * BUY fills contribute ``-(qty × fill_price)`` (cash outflow).
      * SELL fills contribute ``+(qty × fill_price)`` (cash inflow).

    This is the realized P&L for intra-day round-trips. Positions held
    overnight are excluded per swing-horizon scope. When ``pnl_usd`` is
    present in the fill payload (future plans may compute it at fill time),
    it takes precedence.

    :param session: Open async session (caller manages transaction).
    :param user_id: Filter to this user's events.
    :param today_et: Calendar date in the America/New_York timezone.
    :returns: Populated :class:`DigestData`.
    """
    # Convert the ET date window to UTC ISO strings for the WHERE clause.
    day_start_et = datetime(
        today_et.year, today_et.month, today_et.day, 0, 0, 0, tzinfo=_ET
    )
    day_end_et = day_start_et + timedelta(days=1)
    day_start_utc = day_start_et.astimezone(UTC).isoformat()
    day_end_utc = day_end_et.astimezone(UTC).isoformat()

    # SELECT events within today's window for this user.
    rows = (
        await session.execute(
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.ts >= day_start_utc,
                Event.ts < day_end_utc,
            )
            .order_by(Event.id.asc())
        )
    ).scalars().all()

    fills_count = 0
    errors_count = 0
    cap_rejections_count = 0
    gross_pnl_usd = Decimal("0")
    per_strategy: dict[str, dict[str, Any]] = {}
    # Plan 05-05 Task 3: auto-execution + anomaly-demotion accumulation.
    auto_exec_count = 0
    auto_exec_strategies: set[str] = set()
    # Latest-wins per strategy: a strategy demoted then (somehow) re-demoted
    # today renders once; the dict preserves the most recent drawdown_pct.
    anomaly_by_strategy: dict[str, str] = {}

    for row in rows:
        try:
            # payload_json is the canonicalized JSON from append_event — parse it.
            outer = json.loads(row.payload_json)
            # The canonical-subset JSON wraps the inner payload under "payload".
            payload = outer.get("payload", outer)
        except (json.JSONDecodeError, TypeError):
            continue

        if row.event_type == "fill":
            fills_count += 1
            strat_name: str = payload.get("strategy_name", "_unknown_")

            # Determine per-fill P&L contribution.
            if "pnl_usd" in payload:
                fill_pnl = Decimal(str(payload["pnl_usd"]))
            else:
                # Sign convention: BUY = cash out (negative), SELL = cash in (positive).
                try:
                    qty = Decimal(str(payload.get("filled_qty", "0")))
                    price = Decimal(str(payload.get("filled_avg_price", "0")))
                    side = str(payload.get("side", "buy")).lower()
                    fill_pnl = (price * qty) if side == "sell" else -(price * qty)
                except Exception:  # noqa: BLE001
                    fill_pnl = Decimal("0")

            gross_pnl_usd += fill_pnl

            if strat_name not in per_strategy:
                per_strategy[strat_name] = {
                    "pnl_usd": Decimal("0"),
                    "fills_count": 0,
                }
            per_strategy[strat_name]["pnl_usd"] += fill_pnl
            per_strategy[strat_name]["fills_count"] += 1

        elif row.event_type == "error":
            errors_count += 1
        elif row.event_type == "cap_rejection":
            cap_rejections_count += 1
        elif row.event_type == "auto_execution":
            # Plan 05-05 Task 3 (SC-2): count auto-executed decisions and
            # attribute them per-strategy so the digest can mark auto fills
            # with 🤖 and render the aggregate "{N} across {M} strategies".
            auto_exec_count += 1
            auto_name = payload.get("strategy_name")
            if auto_name:
                auto_exec_strategies.add(auto_name)
        elif row.event_type == "anomaly_demotion":
            # Plan 05-05 Task 3 (TRUST-04): collect the strategy + drawdown so
            # the digest folds in an anomaly-demotion summary line.
            anom_name = payload.get("strategy_name", "_unknown_")
            anomaly_by_strategy[anom_name] = str(payload.get("drawdown_pct", ""))

    # Rough open-positions count: distinct tickers in FILLED proposals for this user.
    filled_rows = (
        await session.execute(
            select(ProposalRow.payload_json)
            .where(
                ProposalRow.user_id == user_id,
                ProposalRow.status == "FILLED",
                ProposalRow.account_mode.in_(["PAPER", "LIVE"]),
            )
        )
    ).scalars().all()

    open_tickers: set[str] = set()
    for pj in filled_rows:
        try:
            pp = json.loads(pj)
            ticker = pp.get("ticker") or ""
            if ticker:
                open_tickers.add(ticker)
        except Exception:  # noqa: BLE001
            pass

    anomaly_demotions = [
        {"strategy_name": name, "drawdown_pct": dd}
        for name, dd in anomaly_by_strategy.items()
    ]

    return DigestData(
        fills_count=fills_count,
        gross_pnl_usd=gross_pnl_usd,
        per_strategy=per_strategy,
        errors_count=errors_count,
        cap_rejections_count=cap_rejections_count,
        open_positions_count=len(open_tickers),
        auto_exec_count=auto_exec_count,
        auto_exec_strategies=auto_exec_strategies,
        anomaly_demotions=anomaly_demotions,
    )


# ---------------------------------------------------------------------------
# Block Kit builder — per UI-SPEC §Surface 6
# ---------------------------------------------------------------------------


def _build_digest_blocks(data: DigestData, today_iso: str) -> list[dict[str, Any]]:
    """Build the Block Kit block list for the daily P&L DM per UI-SPEC §Surface 6.

    :param data: Aggregated metrics from :func:`_aggregate_today_events`.
    :param today_iso: ISO date string for the header (``YYYY-MM-DD``).
    :returns: List of Block Kit block dicts.
    """
    settings = get_settings()

    # Gross P&L sign glyph.
    pnl_glyph = "📈" if data.gross_pnl_usd >= 0 else "📉"
    gross_text = (
        f"{pnl_glyph} *Gross P&L:* "
        f"`${float(data.gross_pnl_usd):+,.2f}` across {data.fills_count} fills"
    )

    # Per-strategy breakdown. Plan 05-05 Task 3 (SC-2): append ` 🤖` to lines
    # whose strategy auto-executed at least once today.
    if data.per_strategy:
        strategy_lines = "\n".join(
            (
                f"• `{name}` — `${float(info['pnl_usd']):+,.2f}` "
                f"({info['fills_count']} fills)"
                + (" 🤖" if name in data.auto_exec_strategies else "")
            )
            for name, info in data.per_strategy.items()
        )
        per_strat_text = f"*Per-strategy P&L:*\n{strategy_lines}"
    else:
        per_strat_text = "*Per-strategy P&L:* _no fills today_"

    # Plan 05-05 Task 3 (SC-2 / D-T18): aggregate auto-execution line — the
    # digest is the SC-2 review surface for auto-executed decisions.
    auto_exec_text: str | None = None
    if data.auto_exec_count > 0:
        n_strategies = len(data.auto_exec_strategies)
        auto_exec_text = (
            f"🤖 *Auto-executed today:* {data.auto_exec_count} "
            f"trade{'s' if data.auto_exec_count != 1 else ''} across "
            f"{n_strategies} strateg{'ies' if n_strategies != 1 else 'y'}"
        )

    # Plan 05-05 Task 3 (TRUST-04): anomaly-demotion summary line.
    anomaly_text: str | None = None
    if data.anomaly_demotions:
        parts: list[str] = []
        for d in data.anomaly_demotions:
            dd = d.get("drawdown_pct", "")
            # drawdown_pct is a fraction-string ("0.12") — render as a whole
            # percent ("−12%") to match the UI-SPEC §6c deterministic copy.
            try:
                pct_whole = f"{Decimal(str(dd)) * Decimal('100'):.0f}"
            except Exception:  # noqa: BLE001
                pct_whole = str(dd)
            parts.append(f"{d.get('strategy_name', '?')} (−{pct_whole}% single-day)")
        anomaly_text = "🛑 *Anomaly demotions today:* " + ", ".join(parts)

    # Counts context line.
    counts_text = (
        f"📂 *Open positions:* {data.open_positions_count}   "
        f"✅ *Fills today:* {data.fills_count}   "
        f"❌ *Errors today:* {data.errors_count}"
    )

    blocks: list[dict[str, Any]] = [
        # 0. Header — UI-SPEC §Surface 6 header block.
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Daily P&L — {today_iso}",
                "emoji": True,
            },
        },
        # 1. Gross P&L section with sign glyph.
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": gross_text,
            },
        },
        # 2. Per-strategy breakdown section.
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": per_strat_text,
            },
        },
    ]

    # 2b. Auto-execution aggregate line (Surface 4c) — only when present.
    if auto_exec_text is not None:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": auto_exec_text},
            }
        )

    # 2c. Anomaly-demotion summary line (Surface 6c) — only when present.
    if anomaly_text is not None:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": anomaly_text},
            }
        )

    blocks.extend(
        [
            # 3. Counts context block.
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": counts_text,
                    }
                ],
            },
            # 4. Actions footer with dashboard URL button.
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open dashboard"},
                        "url": f"{settings.dashboard_url}/approvals",
                    }
                ],
            },
        ]
    )
    return blocks


# ---------------------------------------------------------------------------
# DM dispatch helper — routes through quiet-hours wrapper
# ---------------------------------------------------------------------------


async def _send_dm_blocks_respecting_quiet_hours(
    user_id: str,
    *,
    blocks: list[dict[str, Any]],
    category: str,
    fallback: str = "",
) -> bool:
    """Send a Block Kit DM through the quiet-hours gate (PATTERNS §2e).

    Routes through :func:`gekko.execution.executor._send_slack_dm_blocks_respecting_quiet_hours`
    so the identity-split fix (quick task 260612-nlv) and the D-48 quiet-hours
    semantics apply. The ``daily_pnl`` category is ROUTINE — it defers when
    16:30 ET falls within the user's quiet window.

    :returns: ``True`` if the DM was dispatched; ``False`` if suppressed by quiet
        hours. CR-03: callers use this bool to write an honest audit event.
    """
    _BYPASS_CATEGORIES = frozenset({"kill_active", "executor_error", "first_live_fill"})

    if category in _BYPASS_CATEGORIES:
        from gekko.execution.executor import _send_slack_dm_blocks

        # bypass-category: bypass-dispatch — fire directly.
        await _send_slack_dm_blocks(user_id, blocks=blocks, fallback=fallback)
        return True

    # Routine category — consult the quiet-hours predicate.
    from gekko.approval.quiet_hours import _resolve_quiet_hours

    try:
        in_window = await _resolve_quiet_hours(user_id, datetime.now(UTC))
    except Exception:  # noqa: BLE001
        log.exception(
            "daily_pnl.quiet_hours_predicate_failed",
            user_id=user_id,
            category=category,
        )
        in_window = False

    if in_window:
        log.debug(
            "daily_pnl.dm_suppressed",
            user_id=user_id,
            category=category,
        )
        return False

    # Outside quiet window — send the DM.
    from gekko.execution.executor import _send_slack_dm_blocks

    await _send_slack_dm_blocks(user_id, blocks=blocks, fallback=fallback)
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def send_daily_pnl_digest(*, user_id: str) -> bool:
    """Send the daily P&L digest DM to the operator.

    Called by the APScheduler cron job registered in
    :func:`gekko.scheduler.jobs.register_daily_pnl_cron`.

    :param user_id: Internal gekko user id.
    :returns: ``True`` if a DM was dispatched (or attempted), ``False`` if
        skipped (market-closed day per D-59, or other early-return condition).

    D-59 NYSE schedule gate fires FIRST: if today is a weekend or NYSE
    holiday, the function returns ``False`` without touching the DB or the
    Slack API.

    D-48 quiet-hours: ``daily_pnl`` is ROUTINE. The DM fires via
    :func:`_send_dm_blocks_respecting_quiet_hours` which may defer it when
    the operator's quiet window is active.
    """
    # ---- D-59: NYSE schedule gate — check before any DB or Slack work. ----
    nyse = mcal.get_calendar("NYSE")
    today_et = datetime.now(_ET).date()
    schedule = nyse.schedule(start_date=today_et, end_date=today_et)

    if schedule.empty:
        log.info("daily_pnl.market_closed_skip", date=str(today_et))
        return False

    today_iso = str(today_et)

    # ---- Aggregate today's audit-log events. ----
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            data = await _aggregate_today_events(session, user_id, today_et)

        # ---- Build the Block Kit blocks per UI-SPEC §Surface 6. ----
        blocks = _build_digest_blocks(data, today_iso)

        # ---- Dispatch via quiet-hours-aware wrapper (D-48 routine category). ----
        # CR-03 fix: capture the bool return so the audit event can record actual
        # delivery status instead of always claiming the DM was sent.
        dispatched = await _send_dm_blocks_respecting_quiet_hours(
            user_id,
            blocks=blocks,
            category="daily_pnl",
            fallback=f"Daily P&L — {today_iso}",
        )

        # ---- Audit event: record actual delivery status (D-45 / T-03-06-04). ----
        # CR-03: the event is ALWAYS written (suppressed or delivered) so the audit
        # trail is complete. The delivered/suppressed_by_quiet_hours fields reflect
        # what actually happened — never silently claim sent when suppressed.
        gross_pnl_str = f"{float(data.gross_pnl_usd):+,.2f}"
        async with sf() as session, session.begin():
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,  # global event — not strategy-scoped
                event_type="daily_pnl",
                payload=normalize_decimals(
                    {
                        "date": today_iso,
                        "gross_pnl": gross_pnl_str,
                        "fills_count": data.fills_count,
                        "errors_count": data.errors_count,
                        "delivered": dispatched,
                        "suppressed_by_quiet_hours": not dispatched,
                    }
                ),
            )

        log.info(
            "daily_pnl.sent",
            user_id=user_id,
            date=today_iso,
            gross_pnl=gross_pnl_str,
            fills_count=data.fills_count,
            errors_count=data.errors_count,
            delivered=dispatched,
        )
        return True

    finally:
        if engine is not None:
            await engine.dispose()
