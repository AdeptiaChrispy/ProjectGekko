"""Clean-approval streak scanner — Plan 05-02 Task 1 (TRUST-01 / TRUST-02).

The eligibility authority for promoting a strategy to ``auto-within-caps``.
It is a deterministic *backward scan* of the append-only events log — NOT a
counter column (D-14): the audit log is the single source of truth, so the
streak can never drift from the recorded history.

Contract (RESEARCH Pattern 4 / D-T01..D-T05):

  * Approvals partition by ``(strategy_name, account_mode)`` — a different
    strategy or a different mode (PAPER vs LIVE) does NOT bleed into this
    streak (D-T01 / D-T03).
  * A ``cap_rejection`` for this strategy inside the window zeroes
    eligibility and records ``last_breach_date`` (D-T02).
  * The window boundary is the most-recent ``trust_demoted`` /
    ``anomaly_demotion`` event for this strategy — the scan stops there.
    A ``trust_demoted`` with ``reason="material_edit"`` sets
    ``block_reason="material_edit_reset"`` and ``last_reset_date`` (D-T05).
  * ``threshold`` defaults to 10 (D-T01); ``eligible`` requires
    ``clean_count >= threshold`` AND ``block_reason is None``.

The payloads carry ``strategy_name`` + ``account_mode`` because Plan 05-01
enriched the ``approval`` / ``cap_rejection`` writers at write time (TOCTOU-
safe, sourced from the locked rows). This scanner only READS that attribution.

No ``claude_agent_sdk`` import — this module sits on the trust/eligibility
path; LLM bytes never reach it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.db.models import Event
from gekko.logging_config import get_logger

log = get_logger(__name__)

# D-T01: 10 clean approvals (zero cap-breaches) in the window promote-eligible.
_DEFAULT_THRESHOLD = 10

# IN-02 lesson from _hard_caps: cap the backward scan. A window will never
# realistically hold more than a few hundred approvals before a reset; 1000
# is comfortably above any realistic streak depth.
_SCAN_LIMIT = 1000

# Boundary event types: the most-recent of these for this strategy closes the
# streak window (the scan stops at it).
_BOUNDARY_TYPES = frozenset({"trust_demoted", "anomaly_demotion"})


@dataclass(frozen=True)
class StreakResult:
    """The eligibility verdict consumed verbatim by UI-SPEC Surface 5.

    :param clean_count: Number of clean ``approval`` events for this
        (strategy, mode) since the window boundary (and since any
        ``cap_rejection`` that zeroed it).
    :param threshold: Approvals required for eligibility (default 10).
    :param eligible: ``clean_count >= threshold`` AND ``block_reason is None``.
    :param block_reason: ``None`` when eligible; otherwise one of
        ``"insufficient_streak"`` / ``"cap_breach"`` / ``"material_edit_reset"``.
    :param last_breach_date: ISO ts of the most-recent in-window
        ``cap_rejection``, else ``None``.
    :param last_reset_date: ISO ts of the window-boundary demotion, else
        ``None``.
    """

    clean_count: int
    eligible: bool
    block_reason: str | None
    last_breach_date: str | None
    last_reset_date: str | None
    threshold: int = _DEFAULT_THRESHOLD


def _unwrap(row: Event) -> dict | None:
    """Parse ``payload_json`` and return the inner payload, or None on error.

    ``payload_json`` is the canonical subset ``{event_type, payload, ts,
    user_id}`` (see :func:`gekko.audit.log.append_event`); the inner business
    payload lives under ``"payload"``. We tolerate a bare dict too, matching
    the house ``outer.get("payload", outer)`` idiom.
    """
    try:
        outer = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(outer, dict):
        return None
    payload = outer.get("payload", outer)
    return payload if isinstance(payload, dict) else None


async def compute_clean_streak(
    *,
    session: AsyncSession,
    user_id: str,
    strategy_name: str,
    account_mode: str,
) -> StreakResult:
    """Compute the clean-approval streak for ``(strategy_name, account_mode)``.

    Deterministic backward scan over this user's events (id DESC). Stops at
    the most-recent boundary demotion for this strategy. Within the window,
    counts matching ``approval`` events and zeroes eligibility on a matching
    ``cap_rejection`` (D-T02).

    :param session: An async session bound to the per-user SQLCipher engine.
        The caller owns the transaction — this function only READs.
    """
    rows = (
        await session.execute(
            select(Event)
            .where(Event.user_id == user_id)
            .order_by(Event.id.desc())
            .limit(_SCAN_LIMIT)
        )
    ).scalars().all()

    clean_count = 0
    last_breach_date: str | None = None
    last_reset_date: str | None = None
    block_reason: str | None = None

    for row in rows:
        payload = _unwrap(row)
        if payload is None:
            continue

        # The window boundary is the most-recent demotion for THIS strategy.
        if row.event_type in _BOUNDARY_TYPES:
            if payload.get("strategy_name") != strategy_name:
                continue  # a different strategy's demotion — keep scanning
            last_reset_date = row.ts
            reason = payload.get("reason")
            if reason == "material_edit":
                block_reason = "material_edit_reset"
            break  # stop the scan — everything older is out of the window

        # cap_rejection mid-window: zeroes eligibility (D-T02) AND closes the
        # clean-count window — clean_count is the run of clean approvals
        # SINCE the most-recent breach. It carries strategy_name but NOT
        # account_mode (rejections fire before the mode-attribution split);
        # attribute by strategy_name alone.
        if row.event_type == "cap_rejection":
            if payload.get("strategy_name") != strategy_name:
                continue
            last_breach_date = row.ts  # most-recent breach (id DESC, first hit)
            block_reason = "cap_breach"
            break  # stop — approvals older than this breach do not count

        # approval: count only matching (strategy_name, account_mode) — no
        # cross-strategy / cross-mode bleed (D-T01 / D-T03).
        if row.event_type == "approval":
            if (
                payload.get("strategy_name") == strategy_name
                and payload.get("account_mode") == account_mode
            ):
                clean_count += 1
            continue

    if len(rows) >= _SCAN_LIMIT:
        log.warning(
            "trust.streak_scan_at_limit",
            user_id=user_id,
            strategy_name=strategy_name,
            account_mode=account_mode,
            limit=_SCAN_LIMIT,
        )

    threshold = _DEFAULT_THRESHOLD
    if block_reason is None and clean_count < threshold:
        block_reason = "insufficient_streak"

    eligible = clean_count >= threshold and block_reason is None

    return StreakResult(
        clean_count=clean_count,
        threshold=threshold,
        eligible=eligible,
        block_reason=block_reason,
        last_breach_date=last_breach_date,
        last_reset_date=last_reset_date,
    )


__all__: tuple[str, ...] = ("StreakResult", "compute_clean_streak")
