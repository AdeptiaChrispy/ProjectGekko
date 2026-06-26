"""Strategy autonomy-axis trust helpers — Plan 05-02 Task 2 (TRUST-01/05/06).

The autonomy axis is independent of the live/paper axis (``promotion.py``).
A strategy's ``trust_level`` is one of:

  * ``"propose-only"`` (default) — every decision goes to HITL.
  * ``"auto-within-caps"`` — the runtime auto-executes proposals that pass
    OrderGuard, within the per-strategy + portfolio caps.

This module is the **SOLE writer** of ``trust_level = "auto-within-caps"``.
That invariant is locked by an AST gate
(:mod:`tests.unit.test_trust_safety_invariants`): any other module that
assigns the literal is a backdoor past the clean-streak eligibility gate, so
the gate fails the build. Promotion eligibility is computed by
:func:`gekko.strategy.streak.compute_clean_streak` — the route/CLI re-check it
server-side before calling :func:`promote_strategy_to_auto` (D-T18b).

Mirrors ``promotion.py`` structurally: the module-local
:func:`_get_session_factory` shim, ``async with sf() as session,
session.begin():`` + ``finally: engine.dispose()``, dedicated first-class
event types (``trust_promoted`` / ``trust_demoted``), ``strategy_id=None``
with ``strategy_name`` in the payload, and ``normalize_decimals(payload)``.

No ``claude_agent_sdk`` import — these helpers sit on the trust/promotion
path; LLM bytes never reach them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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

#: The autonomy trust level. This module is the SOLE writer of this literal
#: (enforced by tests/unit/test_trust_safety_invariants.py).
TRUST_AUTO = "auto-within-caps"
#: The default / demoted trust level.
TRUST_PROPOSE_ONLY = "propose-only"


# ---------------------------------------------------------------------------
# Module-level test seam (verbatim from promotion.py)
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


async def promote_strategy_to_auto(
    *,
    user_id: str,
    strategy_name: str,
    account_mode: str = "PAPER",
    clean_count: int | None = None,
) -> None:
    """Promote a strategy to ``auto-within-caps`` (D-T01).

    UPSERTs the :class:`StrategyMetadata` row keyed by
    ``(user_id, strategy_name)``: sets ``trust_level="auto-within-caps"`` and
    ``trust_promoted_at=<iso>``. Emits a ``trust_promoted`` audit event with
    ``strategy_name`` + ``account_mode`` in the payload (``strategy_id=None``).

    The CALLER is responsible for verifying eligibility
    (:func:`gekko.strategy.streak.compute_clean_streak`) before calling this —
    this helper does not re-check, so the AST gate + route/CLI guard are the
    enforcement (D-T18b).
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
                        trust_level=TRUST_AUTO,
                        trust_promoted_at=now_iso,
                    )
                )
            else:
                existing.trust_level = TRUST_AUTO
                existing.trust_promoted_at = now_iso
            payload: dict[str, object] = {
                "strategy_name": strategy_name,
                "account_mode": account_mode,
                "trust_promoted_at": now_iso,
            }
            if clean_count is not None:
                payload["clean_count"] = clean_count
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="trust_promoted",
                payload=normalize_decimals(payload),
            )
        log.info(
            "strategy.promoted_to_auto",
            user_id=user_id,
            strategy_name=strategy_name,
            account_mode=account_mode,
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def demote_strategy_from_auto(
    *,
    user_id: str,
    strategy_name: str,
    reason: str,
    drawdown_pct: str | None = None,
) -> None:
    """Demote a strategy back to ``propose-only`` (D-T04 / D-T05).

    Sets ``trust_level="propose-only"`` and emits a ``trust_demoted`` event
    carrying ``strategy_name`` + ``reason`` (and ``drawdown_pct`` for anomaly
    demotions). The ``trust_demoted`` event is the clean-streak WINDOW BOUNDARY
    — once written, the streak scanner stops there, so a demotion resets the
    streak (``reason="material_edit"`` is modelled as a demotion so a material
    edit resets trust without snapshot-diffing — RESEARCH Pattern 4).

    Idempotent: demoting a strategy that is already ``propose-only`` still
    writes the event (the audit trail records the operator/anomaly intent) but
    leaves the column unchanged. A missing metadata row is a no-op + warning.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            existing = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if existing is None:
                log.warning(
                    "strategy.demote_auto_no_metadata_row",
                    user_id=user_id,
                    strategy_name=strategy_name,
                    reason=reason,
                )
                return
            existing.trust_level = TRUST_PROPOSE_ONLY
            payload: dict[str, object] = {
                "strategy_name": strategy_name,
                "reason": reason,
            }
            if drawdown_pct is not None:
                payload["drawdown_pct"] = drawdown_pct
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="trust_demoted",
                payload=normalize_decimals(payload),
            )
        log.info(
            "strategy.demoted_from_auto",
            user_id=user_id,
            strategy_name=strategy_name,
            reason=reason,
        )
    finally:
        if engine is not None:
            await engine.dispose()


#: Default capital ceiling read when the StrategyMetadata row is missing or the
#: column is NULL (D-T16). Mirrors _capital_ceiling.DEFAULT_CAPITAL_CEILING_USD.
DEFAULT_CAPITAL_CEILING_USD = "1000.00"


async def set_capital_ceiling(
    *,
    user_id: str,
    strategy_name: str,
    new_ceiling_usd: str,
) -> tuple[str, str]:
    """Set a strategy's capital ceiling (TRUST-03 / D-T14 / D-T17).

    UPSERTs ``StrategyMetadata.capital_ceiling_usd`` (Decimal-as-TEXT) and appends
    a ``capital_scaled`` audit event with ``{strategy_name, old_ceiling_usd,
    new_ceiling_usd, direction}`` in the payload.

    Capital scaling is a SEPARATE rung from autonomy: this helper MUST NOT touch
    ``trust_level`` or write any trust event (D-T17). The caller (route / CLI)
    owns the confirm-on-increase gate; this helper just records the change.

    Returns ``(old_ceiling_str, new_ceiling_str)`` for the caller's success copy.
    """
    new_dec = Decimal(str(new_ceiling_usd))
    new_str = str(new_dec)
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            existing = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            old_raw = (
                existing.capital_ceiling_usd
                if existing is not None
                and existing.capital_ceiling_usd is not None
                else DEFAULT_CAPITAL_CEILING_USD
            )
            old_dec = Decimal(str(old_raw))
            old_str = str(old_dec)
            if existing is None:
                session.add(
                    StrategyMetadata(
                        user_id=user_id,
                        strategy_name=strategy_name,
                        capital_ceiling_usd=new_str,
                    )
                )
            else:
                existing.capital_ceiling_usd = new_str
            direction = "increase" if new_dec > old_dec else "decrease"
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="capital_scaled",
                payload=normalize_decimals(
                    {
                        "strategy_name": strategy_name,
                        "old_ceiling_usd": old_str,
                        "new_ceiling_usd": new_str,
                        "direction": direction,
                    }
                ),
            )
        log.info(
            "strategy.capital_scaled",
            user_id=user_id,
            strategy_name=strategy_name,
            old_ceiling_usd=old_str,
            new_ceiling_usd=new_str,
        )
    finally:
        if engine is not None:
            await engine.dispose()
    return old_str, new_str


async def load_trust_level(
    *,
    user_id: str,
    strategy_name: str,
    account_mode: str = "PAPER",  # noqa: ARG001 - axis kept for caller parity
) -> str:
    """Return the strategy's ``trust_level`` or ``"propose-only"`` default.

    ``account_mode`` is accepted for caller-signature parity with the
    auto-branch (which reads trust per proposal mode); the trust level itself
    is stored per-strategy, not per-mode, so the argument is currently
    informational. A missing metadata row defaults to ``"propose-only"``.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if row is None or row.trust_level is None:
                return TRUST_PROPOSE_ONLY
            return row.trust_level
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = (
    "DEFAULT_CAPITAL_CEILING_USD",
    "TRUST_AUTO",
    "TRUST_PROPOSE_ONLY",
    "demote_strategy_from_auto",
    "load_trust_level",
    "promote_strategy_to_auto",
    "set_capital_ceiling",
)
