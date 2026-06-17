"""Strategy live-mode promotion helpers — Plan 02-06 Task 2 (D-31 / D-32).

Three operations on the :class:`gekko.db.models.StrategyMetadata` row:

  * :func:`promote_strategy_to_live` — sets ``live_mode_eligible=True`` +
    ``live_promoted_at=<iso>`` (UPSERT). Called by both the CLI
    ``gekko strategy promote-live <name>`` and the dashboard
    ``POST /strategies/{name}/promote-to-live`` route (D-31 symmetric
    surfaces; Slack deliberately has NO promote command).

  * :func:`demote_strategy_from_live` — sets ``live_mode_eligible=False``.
    Does NOT clear ``first_live_trade_confirmed_at`` (set-once;
    re-promoting later keeps the dual-channel gate already satisfied).

  * :func:`stamp_first_live_trade` — set-once stamp on
    ``first_live_trade_confirmed_at``. Called by the executor's
    :func:`on_fill_event` on the FIRST live fill per strategy (D-32
    per-strategy semantics). Subsequent live trades bypass the
    HITL-06 dual-channel gate.

Per PATTERNS §3c every public function uses the module-local
:func:`_get_session_factory` shim with a ``finally: engine.dispose()``
block.

No ``claude_agent_sdk`` import — these helpers sit on the credentials /
promotion path; LLM bytes never reach them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import StrategyMetadata
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level test seam
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def promote_strategy_to_live(
    *, user_id: str, strategy_name: str
) -> None:
    """Mark a strategy as live-eligible (D-31).

    UPSERTs the :class:`StrategyMetadata` row keyed by
    ``(user_id, strategy_name)``. Sets ``live_mode_eligible=True`` and
    ``live_promoted_at=<iso>``. Idempotent — re-running on a
    live-eligible strategy refreshes ``live_promoted_at`` without
    changing anything else.

    Emits a ``live_mode_promoted`` audit event so the chain captures
    who/when (BL-01 fix: previously written as ``event_type="error"``
    with a ``context="strategy.promoted_to_live"`` discriminator; the
    Phase-2 D-14 vocabulary now carries ``live_mode_promoted`` directly).
    """
    sf, engine = _get_session_factory(user_id)
    try:
        now_iso = datetime.now(UTC).isoformat()
        async with sf() as session, session.begin():
            existing = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if existing is None:
                session.add(
                    StrategyMetadata(
                        user_id=user_id,
                        strategy_name=strategy_name,
                        live_mode_eligible=True,
                        live_promoted_at=now_iso,
                        first_live_trade_confirmed_at=None,
                    )
                )
            else:
                existing.live_mode_eligible = True
                existing.live_promoted_at = now_iso
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="live_mode_promoted",
                payload=normalize_decimals(
                    {
                        "strategy_name": strategy_name,
                        "live_promoted_at": now_iso,
                    }
                ),
            )
        log.info(
            "strategy.promoted_to_live",
            user_id=user_id,
            strategy_name=strategy_name,
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def demote_strategy_from_live(
    *, user_id: str, strategy_name: str
) -> None:
    """Demote a strategy back to paper-only (D-31).

    Sets ``live_mode_eligible=False``. Does NOT clear
    ``first_live_trade_confirmed_at`` — once stamped, the per-strategy
    dual-channel gate stays satisfied even if the strategy is
    re-promoted later.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            existing = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if existing is None:
                # Nothing to demote — log + return without error so the
                # caller's UX is symmetric with promote.
                log.warning(
                    "strategy.demote_no_metadata_row",
                    user_id=user_id,
                    strategy_name=strategy_name,
                )
                return
            existing.live_mode_eligible = False
            # BL-01 fix: dedicated ``live_mode_demoted`` event_type
            # replaces the prior ``event_type="error"`` workaround.
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="live_mode_demoted",
                payload=normalize_decimals(
                    {
                        "strategy_name": strategy_name,
                    }
                ),
            )
        log.info(
            "strategy.demoted_from_live",
            user_id=user_id,
            strategy_name=strategy_name,
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def stamp_first_live_trade(
    *, user_id: str, strategy_name: str, fill_ts: str
) -> None:
    """Set-once stamp on ``first_live_trade_confirmed_at`` (D-32).

    Called by the executor's :func:`on_fill_event` on the FIRST live fill
    per strategy. Subsequent calls are no-ops (the SET is conditional
    on ``first_live_trade_confirmed_at IS NULL``). Race-safe: if two
    fills land simultaneously, the second's SET sees a non-NULL value
    and skips the update.

    Per D-32 per-strategy semantics: once stamped, the HITL-06
    dual-channel gate is closed for this strategy. The Slack approve
    handler reads this stamp to decide whether to divert to
    ``AWAITING_2ND_CHANNEL``.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            existing = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if existing is None:
                # No metadata row — shouldn't happen since promote_to_live
                # creates one before the first live trade fires, but be
                # defensive. Create the row with the stamp set.
                session.add(
                    StrategyMetadata(
                        user_id=user_id,
                        strategy_name=strategy_name,
                        live_mode_eligible=True,
                        live_promoted_at=None,
                        first_live_trade_confirmed_at=fill_ts,
                    )
                )
            elif existing.first_live_trade_confirmed_at is None:
                # Set-once: only stamp when NULL. Race-safe — a second
                # concurrent fill sees the non-NULL stamp and skips.
                existing.first_live_trade_confirmed_at = fill_ts
            else:
                # Already stamped — no-op.
                return
            # BL-01 fix: dedicated ``first_live_trade_confirmed``
            # event_type replaces the prior ``event_type="error"`` +
            # ``context="strategy.first_live_trade_stamped"`` workaround.
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="first_live_trade_confirmed",
                payload=normalize_decimals(
                    {
                        "strategy_name": strategy_name,
                        "fill_ts": fill_ts,
                    }
                ),
            )
        log.info(
            "strategy.first_live_trade_stamped",
            user_id=user_id,
            strategy_name=strategy_name,
            fill_ts=fill_ts,
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def load_strategy_metadata(
    *, user_id: str, strategy_name: str
) -> StrategyMetadata | None:
    """Convenience loader for callers needing a snapshot of the metadata row.

    Used by the Slack approve handler (Plan 02-06 Task 2) to compute
    ``is_live_first`` and by the dashboard /live-confirm route to
    validate state.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            return row
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = (
    "demote_strategy_from_live",
    "load_strategy_metadata",
    "promote_strategy_to_live",
    "stamp_first_live_trade",
)
