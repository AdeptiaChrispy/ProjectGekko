"""Stale-proposal expiry sweep — Plan 03-04 Task 1 (HITL-03).

APScheduler fires :func:`expire_stale_proposals` every 60 seconds.  The sweep
finds PENDING / AWAITING_2ND_CHANNEL proposals whose ``expires_at`` has passed,
transitions them to EXPIRED, writes the ``expiration`` audit event, mutates
the original Slack card via ``chat.update``, and DMs the operator.

**Deterministic Python firewall.** This module MUST NOT import the LLM
SDK or the Anthropic client package — the sweep is pure deterministic Python
and must never accidentally pull the LLM stack into the expiry path.
The AST gate ``tests/unit/test_expiry_no_sdk_import.py`` enforces this
invariant at build time.

**Restart-safety.** The registrar in :mod:`gekko.scheduler.jobs` uses
``coalesce=True``, ``max_instances=1``, and ``misfire_grace_time=300`` so a
process restart never double-fires or misses a sweep window.

**Sweep-vs-click race.** The state-machine's idempotent same-state return
(``proposals.py`` lines 139-141) and :mod:`gekko.approval.dedup` are the two
defensive layers. The sweep catches :class:`ValueError` from
:func:`~gekko.approval.proposals.transition_status` when a concurrent
operator click already resolved the proposal — first-write-wins per D-53.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from gekko.approval.proposals import transition_status
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.logging_config import get_logger

if TYPE_CHECKING:
    from gekko.db.session import AsyncSessionLocal
    from sqlalchemy.ext.asyncio import AsyncEngine

log = get_logger(__name__)

#: Default timeout used when strategy.proposal_timeout_minutes is None.
PROPOSAL_TIMEOUT_DEFAULT_MIN: int = 30


# ---------------------------------------------------------------------------
# Module-level test seam (PATTERNS §2d)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple["AsyncSessionLocal", "AsyncEngine | None"]:
    """Build a session factory + owning engine for ``user_id``.

    Tests monkeypatch this symbol to inject a pre-built factory without
    needing real SQLCipher on disk.
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.vault.passphrase import get_passphrase

    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), get_passphrase())
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_strategy_timeout_minutes(strategy_payload_json: str) -> int:
    """Read ``proposal_timeout_minutes`` from the strategy ``payload_json`` blob.

    The payload_json is the canonical JSON of the ``gekko.schemas.Strategy``
    Pydantic model (Plan 01-06).  ``proposal_timeout_minutes`` lives inside
    the JSON object (Plan 03-01 Task 2 extended the Pydantic schema and the
    ProposalWriter stamps it via ``strategy.proposal_timeout_minutes``).

    Falls back to :data:`PROPOSAL_TIMEOUT_DEFAULT_MIN` (30) when the field
    is absent or ``None``.
    """
    try:
        payload = json.loads(strategy_payload_json or "{}")
        minutes = payload.get("proposal_timeout_minutes")
        if isinstance(minutes, int) and minutes > 0:
            return minutes
    except (json.JSONDecodeError, TypeError):
        pass
    return PROPOSAL_TIMEOUT_DEFAULT_MIN


async def _chat_update_expired_card(row: Any) -> None:
    """Mutate the original Slack card in-place to the expired visual.

    Reads ``row.slack_message_ts`` + ``row.slack_message_channel`` from the
    Proposal row (populated by Plan 03-01 Task 4's
    ``_persist_slack_message_coords``). Calls
    ``slack_app.client.chat_update`` with the expired-state blocks from
    :func:`~gekko.reporter.slack.build_proposal_card` (``expired=True`` branch
    added in Plan 03-04 Task 2).

    Best-effort: if ``slack_message_ts`` or ``slack_message_channel`` is
    missing (pre-03-01 rows or rows where the postMessage response was not
    captured), the update is silently skipped — the operator still receives
    the DM from the sweep.

    Per D-53: the caller is responsible for calling this AFTER the DB
    transaction commits (DB-first, side-effects-after ordering invariant
    from kill_switch.py PATTERNS §2d).
    """
    if not row.slack_message_ts or not row.slack_message_channel:
        log.warning(
            "expiry.chat_update.missing_coords",
            proposal_id=row.proposal_id,
            has_ts=bool(row.slack_message_ts),
            has_channel=bool(row.slack_message_channel),
        )
        return

    from gekko.reporter.slack import build_proposal_card
    from gekko.slack.app import slack_app

    # Determine expired_at in a human-friendly local representation.
    # We use the current UTC time (the sweep's processing time) as the
    # "expired at" value in the card; for precision the proposal's
    # expires_at field would be the configured expiry wall-clock time, but
    # the display copy from UI-SPEC §Surface 4 uses the processing time.
    now_utc = datetime.now(UTC)
    expired_at_local = now_utc.strftime("%H:%M UTC")

    # Build a minimal TradeProposal-like object from the proposal payload_json
    # so build_proposal_card receives a typed object.
    try:
        from gekko.schemas.proposal import TradeProposal

        tp = TradeProposal.model_validate_json(row.payload_json)
    except Exception:  # noqa: BLE001
        log.warning(
            "expiry.chat_update.payload_parse_failed",
            proposal_id=row.proposal_id,
        )
        return

    # Resolve timeout_minutes for the status line copy.
    timeout_minutes = _resolve_strategy_timeout_minutes(
        getattr(row, "_strategy_payload_json", "{}")
    )

    try:
        blocks = build_proposal_card(
            tp,
            account_mode=row.account_mode or "PAPER",
            expired=True,
            expired_at_local=expired_at_local,
            timeout_minutes=timeout_minutes,
        )
        await slack_app.client.chat_update(
            channel=row.slack_message_channel,
            ts=row.slack_message_ts,
            blocks=blocks,
        )
        log.info(
            "expiry.chat_update.sent",
            proposal_id=row.proposal_id,
            channel=row.slack_message_channel,
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "expiry.chat_update.failed",
            proposal_id=row.proposal_id,
        )


def _format_expiry_dm(
    row: Any,
    *,
    timeout_minutes: int,
    strategy_name: str,
) -> str:
    """Build the D-53 expiry DM text sent to the operator.

    :param row: Proposal ORM row (carries ``payload_json`` with ticker + side).
    :param timeout_minutes: The configured per-strategy timeout (or default 30).
    :param strategy_name: Human-readable strategy name.
    """
    try:
        from gekko.schemas.proposal import TradeProposal

        tp = TradeProposal.model_validate_json(row.payload_json)
        ticker = tp.ticker
        side = str(tp.side).upper()
    except Exception:  # noqa: BLE001
        ticker = "UNKNOWN"
        side = "UNKNOWN"

    return (
        f"⏰ Your {ticker} {side} proposal expired without action.\n"
        f"Reason: timeout=REJECT (configured at {timeout_minutes} min on strategy {strategy_name}).\n"
        f"To re-run: /gekko run {strategy_name}"
    )


# ---------------------------------------------------------------------------
# Public API — sweep entry point
# ---------------------------------------------------------------------------


async def expire_stale_proposals(*, user_id: str) -> int:
    """Sweep PENDING / AWAITING_2ND_CHANNEL proposals past their ``expires_at``.

    Called by APScheduler's ``IntervalTrigger(seconds=60)`` job registered via
    :func:`~gekko.scheduler.jobs.register_expire_stale_sweep`.

    Per-row work (mirroring the ``executor.market_closed`` branch template from
    PATTERNS §1a / §2g):

    1. SELECT rows matching the expiry criteria (PATTERNS §2d session factory).
    2. For each row:

       a. Open a transaction.
       b. Call :func:`~gekko.approval.proposals.transition_status` (PENDING →
          EXPIRED or AWAITING_2ND_CHANNEL → EXPIRED).  Catches
          :class:`ValueError` for sweep-vs-click races (first-write-wins).
       c. Append ``expiration`` audit event with
          ``normalize_decimals({proposal_id, reason, expired_at, configured_timeout_minutes})``.
       d. OUTSIDE the transaction: ``chat.update`` the original Slack card to
          the expired visual (D-53) and DM the operator (routine category,
          quiet-hours-aware per D-48).

    :param user_id: Internal Gekko user id used to scope the per-user SQLCipher
        engine and as the ``user_id`` on audit events.
    :returns: Count of proposals successfully transitioned to EXPIRED in this run.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        return await _run_sweep(sf, user_id=user_id)
    finally:
        if engine is not None:
            await engine.dispose()


async def _run_sweep(sf: "AsyncSessionLocal", *, user_id: str) -> int:
    """Inner sweep logic — separated to make the engine-disposal boundary clear."""
    from sqlalchemy import and_, select

    from gekko.db.models import Proposal as ProposalRow
    from gekko.db.models import Strategy as StrategyRow

    now_utc = datetime.now(UTC)
    now_iso = now_utc.isoformat()

    # SELECT rows that are past their expires_at.
    # D-61 grandfathering: WHERE expires_at IS NOT NULL ensures pre-migration
    # rows (NULL expires_at) are never swept.
    # Explicit locking via with_for_update() — intent-conveying in SQLite WAL mode.
    async with sf() as session:
        rows = (
            await session.execute(
                select(ProposalRow)
                .where(
                    and_(
                        ProposalRow.status.in_(["PENDING", "AWAITING_2ND_CHANNEL"]),
                        ProposalRow.expires_at.is_not(None),
                        ProposalRow.expires_at <= now_iso,
                    )
                )
                .order_by(ProposalRow.expires_at.asc())
                .with_for_update()
            )
        ).scalars().all()

    if not rows:
        return 0

    log.debug(
        "expiry.sweep.candidates",
        user_id=user_id,
        count=len(rows),
    )

    expired_count = 0
    # Collect (row, timeout_minutes, strategy_name) for side-effects after commit.
    side_effects: list[tuple[Any, int, str]] = []

    for row in rows:
        # Read strategy payload for timeout resolution (needed for DM copy).
        strategy_payload_json = "{}"
        strategy_name = "unknown-strategy"
        async with sf() as session:
            strategy_row = (
                await session.execute(
                    select(StrategyRow).where(
                        StrategyRow.strategy_id == row.strategy_id
                    )
                )
            ).scalar_one_or_none()
            if strategy_row is not None:
                strategy_payload_json = strategy_row.payload_json or "{}"
                strategy_name = strategy_row.strategy_name or "unknown-strategy"

        timeout_minutes = _resolve_strategy_timeout_minutes(strategy_payload_json)

        # Stash for _chat_update_expired_card so it can read strategy context.
        # We attach it dynamically to the ORM row object (not a DB column) so
        # _chat_update_expired_card can access it without a separate DB read.
        row._strategy_payload_json = strategy_payload_json  # type: ignore[attr-defined]

        # DB-first: transition + audit inside one transaction.
        try:
            async with sf() as session, session.begin():
                try:
                    await transition_status(
                        session,
                        row.proposal_id,
                        from_status=row.status,
                        to_status="EXPIRED",
                    )
                except ValueError:
                    # Sweep-vs-click race: the row was already resolved by
                    # an operator click or a concurrent sweep run.
                    # First-write-wins per D-53.  Log + continue.
                    log.info(
                        "expiry.row_already_resolved",
                        proposal_id=row.proposal_id,
                        status=row.status,
                    )
                    continue

                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=row.strategy_id,
                    event_type="expiration",
                    payload=normalize_decimals({
                        "proposal_id": row.proposal_id,
                        "reason": "timeout",
                        "expired_at": now_iso,
                        "configured_timeout_minutes": timeout_minutes,
                    }),
                )

            # Transaction committed — increment and record for side-effects.
            expired_count += 1
            side_effects.append((row, timeout_minutes, strategy_name))

        except Exception:  # noqa: BLE001
            log.exception(
                "expiry.row.error",
                proposal_id=row.proposal_id,
                user_id=user_id,
            )
            # Non-race exception (e.g. DB lock timeout): log and continue to
            # next row rather than aborting the entire sweep.

    # Side-effects OUTSIDE all transactions (DB-first invariant from PATTERNS §2d).
    for row, timeout_minutes, strategy_name in side_effects:
        # 1. Mutate the Slack card in-place to the expired visual (D-53).
        try:
            await _chat_update_expired_card(row)
        except Exception:  # noqa: BLE001
            log.warning(
                "expiry.side_effect.chat_update_failed",
                proposal_id=row.proposal_id,
            )

        # 2. DM the operator — routine category, quiet-hours-aware (D-48).
        # The expiry DM is NOT a bypass category: it is informational and
        # should respect quiet hours (category="routine_fill").
        from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours

        try:
            dm_text = _format_expiry_dm(row, timeout_minutes=timeout_minutes, strategy_name=strategy_name)
            await _send_slack_dm_respecting_quiet_hours(
                user_id,
                dm_text,
                category="routine_fill",
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "expiry.side_effect.dm_failed",
                proposal_id=row.proposal_id,
                user_id=user_id,
            )

    if expired_count:
        log.info(
            "expiry.sweep.done",
            user_id=user_id,
            expired=expired_count,
        )

    return expired_count
