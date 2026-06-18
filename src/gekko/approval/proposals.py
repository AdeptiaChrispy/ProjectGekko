"""Proposal state machine — Plan 01-08 Task 3 (extended by Plan 02-01 Task 5).

The :data:`STATE_TRANSITIONS` set is the canonical lifecycle table for the
``proposals`` row (D-11 / RESEARCH §System Architecture Diagram).

Phase-1 edges (Plan 01-08):

    PENDING   -> APPROVED        (HITL approve)
    PENDING   -> REJECTED        (HITL reject)
    APPROVED  -> EXECUTING       (Executor accepted the work)
    APPROVED  -> FAILED          (Executor refused; e.g. market-hours guard)
    EXECUTING -> FILLED          (TradingStream fill landed)
    EXECUTING -> FAILED          (Broker rejection mid-flight)

Phase-2 dual-channel gate edges (Plan 02-01 Task 5 / BLOCKER #1 closure;
D-32 / HITL-06). The frozenset entries are added in Wave-1 so the DB-layer
CHECK constraint (Alembic 0002) and the state-machine layer are coherent
the moment plan 02-06 wires the dual-channel approve handler — no Wave-2+
state-machine surgery required.

    PENDING               -> AWAITING_2ND_CHANNEL  (Slack approve diverts to
                                                    dashboard for the FIRST
                                                    live trade per HITL-06)
    AWAITING_2ND_CHANNEL  -> APPROVED_LIVE         (dashboard /live-confirm
                                                    fires after dual-channel
                                                    ack)
    AWAITING_2ND_CHANNEL  -> REJECTED              (operator rejects in the
                                                    second channel)
    AWAITING_2ND_CHANNEL  -> EXPIRED               (reserved for a future
                                                    timeout path; the EXPIRED
                                                    status is NOT yet in
                                                    _PROPOSAL_STATUSES — this
                                                    edge is forward-prep so
                                                    plan 02-06 can wire it
                                                    without re-shaping the
                                                    frozenset)
    APPROVED_LIVE         -> EXECUTING             (live-broker hand-off)

The atomic primitive every approval / executor / fill path goes through is
:func:`transition_status` — a SELECT-then-UPDATE walking exactly one row.
The function body is DATA-DRIVEN on the frozenset (PATTERNS §3e) — extending
the data does NOT require body changes. Idempotence: when the row is already
in the target status, the function returns the existing row without raising
(so duplicate Slack actions / duplicate dashboard clicks can't break the
chain). Invalid transitions raise :class:`ValueError`.

Two convenience wrappers — :func:`approve_proposal` and
:func:`reject_proposal` — perform the transition AND append the matching
audit event (``approval`` / ``rejection``) per D-14 / HITL-04. The audit
event payload carries the actor's Slack user id and the originating
``slack_action_id`` so the audit log captures *who* clicked *which* button.

References:
  * .planning/.../01-CONTEXT.md  D-11 lifecycle; HITL-04
  * .planning/.../02-CONTEXT.md  D-32, HITL-06 dual-channel gate
  * .planning/.../01-RESEARCH.md §"Anti-Patterns" — at-least-once delivery
  * src/gekko/audit/log.py       append_event (the chain writer)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.log import append_event
from gekko.db.models import Proposal as ProposalRow

# ---------------------------------------------------------------------------
# Lifecycle table
# ---------------------------------------------------------------------------

#: Canonical set of allowed ``(from_status, to_status)`` transitions.
#:
#: APPROVED -> FAILED is the market-hours rejection path: the Executor
#: (Task 4) refuses to call ``place_order`` outside RTH and flips the row
#: from APPROVED straight to FAILED with an ``error`` audit event.
STATE_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # Phase 1 — Plan 01-08
        ("PENDING", "APPROVED"),
        ("PENDING", "REJECTED"),
        ("APPROVED", "EXECUTING"),
        ("APPROVED", "FAILED"),
        ("EXECUTING", "FILLED"),
        ("EXECUTING", "FAILED"),
        # Phase 2 — Plan 02-01 Task 5 (BLOCKER #1): dual-channel gate edges.
        # WIRING for these transitions lives in plan 02-06 Task 2 (Slack approve
        # diverts to AWAITING_2ND_CHANNEL; dashboard /live-confirm fires
        # APPROVED_LIVE). The frozenset entries land here in Wave 1 so the
        # state-machine + DB layer accept the new states the moment plan 02-06
        # wires them, with no Wave-2+ state-machine surgery required.
        ("PENDING", "AWAITING_2ND_CHANNEL"),
        ("AWAITING_2ND_CHANNEL", "APPROVED_LIVE"),
        ("AWAITING_2ND_CHANNEL", "REJECTED"),
        ("AWAITING_2ND_CHANNEL", "EXPIRED"),
        ("APPROVED_LIVE", "EXECUTING"),
        # Phase 3 — Plan 03-01 Task 3: sweep-side expiry edge per D-50.
        # The sweep (plan 03-03 ``expire_stale_proposals``) calls this edge
        # when ``proposals.expires_at <= utcnow()``. The idempotent same-state
        # return at ``transition_status`` lines 139-141 is preserved — a row
        # already in EXPIRED is a no-op (double-sweep safe).
        ("PENDING", "EXPIRED"),
    }
)


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


async def transition_status(
    session: AsyncSession,
    proposal_id: str,
    *,
    from_status: str,
    to_status: str,
) -> ProposalRow:
    """Atomically move a proposal row from one status to another.

    :param session: An async SQLAlchemy session. The caller owns the
        surrounding transaction — this function only ``flush()``-es so the
        row's ``updated_at`` is visible to a following audit-event write
        in the same transaction.
    :param proposal_id: Primary key of the ``proposals`` row.
    :param from_status: Expected current status. Asserted before mutation.
    :param to_status: Target status. Must be ``(current, to_status)`` in
        :data:`STATE_TRANSITIONS` unless it equals the current status (in
        which case this is a no-op for idempotence).
    :returns: The updated (or unchanged-on-idempotent-call) row.
    :raises ValueError: When the row's current status is neither
        ``from_status`` nor ``to_status``, OR when the transition is not in
        :data:`STATE_TRANSITIONS`. Both conditions indicate a logic bug in
        the caller, not user input — surface loudly.
    """
    row = (
        await session.execute(
            select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
        )
    ).scalar_one()

    # Idempotent: already in target status -> return unchanged.
    if row.status == to_status:
        return row

    if (row.status, to_status) not in STATE_TRANSITIONS:
        msg = (
            f"Invalid proposal status transition: {row.status!r} -> "
            f"{to_status!r} (proposal_id={proposal_id!r})"
        )
        raise ValueError(msg)

    if row.status != from_status:
        msg = (
            f"Proposal {proposal_id!r} not in expected status: "
            f"expected {from_status!r}, found {row.status!r}"
        )
        raise ValueError(msg)

    row.status = to_status
    row.updated_at = datetime.now(UTC).isoformat()
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Approve / Reject — transition + audit event in one transaction
# ---------------------------------------------------------------------------


async def approve_proposal(
    session: AsyncSession,
    proposal_id: str,
    *,
    actor: str,
    extra_payload: dict[str, Any] | None = None,
) -> ProposalRow:
    """HITL approve — PENDING -> APPROVED + append ``approval`` audit event.

    :param session: Async session inside the caller's transaction.
    :param proposal_id: Row to transition.
    :param actor: Slack user id of the approver (``body['user']['id']`` in
        the bolt handler). Recorded on the audit event so the chain
        captures *who* approved.
    :param extra_payload: Optional extra keys merged into the event payload
        (e.g., reason text). Trusted dict — the caller is responsible for
        sanitization.
    """
    row = await transition_status(
        session,
        proposal_id,
        from_status="PENDING",
        to_status="APPROVED",
    )
    payload: dict[str, Any] = {
        "proposal_id": proposal_id,
        "actor": actor,
        "slack_action_id": "approve_proposal",
    }
    if extra_payload:
        payload.update(extra_payload)
    await append_event(
        session,
        user_id=row.user_id,
        strategy_id=row.strategy_id,
        event_type="approval",
        payload=payload,
    )
    return row


async def reject_proposal(
    session: AsyncSession,
    proposal_id: str,
    *,
    actor: str,
    reason: str | None = None,
) -> ProposalRow:
    """HITL reject — PENDING -> REJECTED + append ``rejection`` audit event."""
    row = await transition_status(
        session,
        proposal_id,
        from_status="PENDING",
        to_status="REJECTED",
    )
    payload: dict[str, Any] = {
        "proposal_id": proposal_id,
        "actor": actor,
        "slack_action_id": "reject_proposal",
    }
    if reason is not None:
        payload["reason"] = reason
    await append_event(
        session,
        user_id=row.user_id,
        strategy_id=row.strategy_id,
        event_type="rejection",
        payload=payload,
    )
    return row


async def expire_proposal(
    session: AsyncSession,
    proposal_id: str,
    *,
    reason: str,
    expired_at: str,
    configured_timeout_minutes: int,
) -> ProposalRow:
    """Sweep expiry — PENDING -> EXPIRED + append ``expiration`` audit event.

    Convenience wrapper (mirrors :func:`approve_proposal` / :func:`reject_proposal`)
    that combines the state-machine transition AND the D-50 audit event in one
    call. Used by the ``expire_stale_proposals`` sweep (plan 03-03) and the
    optional dashboard manual-expire path.

    :param session: Async session inside the caller's transaction.
    :param proposal_id: Row to transition.
    :param reason: Human-readable reason for expiry (e.g. "timeout").
    :param expired_at: ISO UTC timestamp when the expiry was processed.
    :param configured_timeout_minutes: The timeout value at proposal-build time
        (``strategy.proposal_timeout_minutes or PROPOSAL_TIMEOUT_DEFAULT_MIN``).
        Included in the audit payload (D-50) so forensic analysis can distinguish
        "default 30-min window" from "operator-configured window".

    :returns: The updated Proposal row in EXPIRED status.
    :raises ValueError: When the transition is invalid (e.g. APPROVED -> EXPIRED).
    """
    from gekko.audit.canonical import normalize_decimals

    row = await transition_status(
        session,
        proposal_id,
        from_status="PENDING",
        to_status="EXPIRED",
    )
    await append_event(
        session,
        user_id=row.user_id,
        strategy_id=row.strategy_id,
        event_type="expiration",
        payload=normalize_decimals({
            "proposal_id": proposal_id,
            "reason": reason,
            "expired_at": expired_at,
            "configured_timeout_minutes": configured_timeout_minutes,
        }),
    )
    return row


__all__: tuple[str, ...] = (
    "STATE_TRANSITIONS",
    "approve_proposal",
    "expire_proposal",
    "reject_proposal",
    "transition_status",
)
