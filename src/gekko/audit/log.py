"""Single-source-of-truth audit writer with SHA-256 hash chain — Plan 01-04 Task 2.

Every ``decision``, ``proposal``, ``approval``, ``rejection``,
``order_submitted``, ``fill``, ``kill_switch``, ``cap_rejection``, and
``error`` event written in Phase 1 — and every future phase — flows through
:func:`append_event`. The function:

1. Reads the previous event row for ``user_id`` (ORDER BY id DESC LIMIT 1) →
   ``prev_hash`` or :data:`GENESIS_PREV_HASH` if this is the user's first
   event.
2. Builds the canonical subset ``{event_type, payload, ts, user_id}`` per
   D-16 / RESEARCH §Pattern 3.
3. Computes ``row_hash = sha256(prev_hash.encode("ascii") +
   canonical.encode("utf-8")).hexdigest()``.
4. Stores the FULL canonical subset string in ``Event.payload_json`` (Pattern
   3 lock-in — verify-time hashing is then a one-liner: ``sha256(prev_hash +
   row.payload_json)``).
5. Returns the inserted ``Event`` row.

**Concurrency model.** Per-user :class:`asyncio.Lock` instances live in the
module-level ``_append_locks`` dict. Concurrent appends for the same user
serialize on that user's lock; appends for different users run in parallel.
Cross-process concurrency is NOT a Phase 1 concern — per D-18, Gekko is a
single-process modular monolith.

**user_id scoping** (D-21). Each user's chain is independent: bob's first
event uses :data:`GENESIS_PREV_HASH`, never alice's last ``row_hash``. The
chain query (``SELECT row_hash FROM events WHERE user_id = :uid ORDER BY id
DESC LIMIT 1``) enforces this naturally. Per D-19, each user also gets their
own SQLCipher-encrypted DB file, so cross-user contamination via the same DB
is a non-issue at runtime — but the user_id-scoped query is still the load-
bearing invariant for any future shared-DB scenario (Plan 06 tests etc.).

References:
  * CONTEXT.md D-14 / D-15 / D-16 — audit log schema + hash chain
  * CONTEXT.md D-18 — single-process modular monolith (asyncio.Lock suffices)
  * CONTEXT.md D-21 — per-user isolation
  * CONTEXT.md Claude's Discretion A11 — GENESIS_PREV_HASH = "0" * 64
  * RESEARCH.md §"Pattern 3" — canonical-subset lock-in
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.canonical import GENESIS_PREV_HASH, canonical_json
from gekko.db.models import Event

# ---------------------------------------------------------------------------
# Per-user lock registry
# ---------------------------------------------------------------------------
#
# A single module-level ``asyncio.Lock()`` would also satisfy the
# concurrent-append test in this plan, but per-user locks let independent
# users append in parallel — which matters as soon as Plan 01-08's Slack
# handler and Plan 01-09's APScheduler both run inside the same event loop.
#
# We use a plain ``dict``; entries are created lazily on first use. The
# registry-creation step is itself guarded by ``_registry_lock`` so two
# concurrent first-use callers for the same brand-new user_id don't race
# the ``setdefault``.

_append_locks: dict[str, asyncio.Lock] = {}
_registry_lock: asyncio.Lock = asyncio.Lock()


async def _lock_for(user_id: str) -> asyncio.Lock:
    """Return the per-user lock, creating it on first use."""
    # Fast path: lock already exists.
    lock = _append_locks.get(user_id)
    if lock is not None:
        return lock
    # Slow path: registry mutation under guard.
    async with _registry_lock:
        return _append_locks.setdefault(user_id, asyncio.Lock())


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


async def append_event(
    session: AsyncSession,
    *,
    user_id: str,
    strategy_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    ts: str | None = None,
) -> Event:
    """Write a single audit event with the SHA-256 hash chain attached.

    :param session: An async SQLAlchemy session bound to the per-user
        SQLCipher engine. The caller is responsible for committing (or
        rolling back) the transaction — ``append_event`` only ``flush()``-es
        so it can return the inserted row's autoincrement ``id``.
    :param user_id: The user the event belongs to. Used both as the
        ``Event.user_id`` column and as part of the canonical-subset hash
        input (D-21).
    :param strategy_id: Optional strategy FK. NULL for global events
        (``kill_switch`` etc.). NOT part of the canonical subset — the chain
        is over ``{event_type, payload, ts, user_id}`` only, so future
        ``strategy_id`` mutations cannot retroactively invalidate the chain.
    :param event_type: One of the D-14 vocabulary values (CheckConstraint at
        the DB layer enforces this — passing an unknown value raises
        ``sqlalchemy.exc.IntegrityError`` at flush time).
    :param payload: Arbitrary JSON-serializable dict — the structured
        rationale (D-15) for ``decision`` / ``proposal`` events lives here.
        Callers handling money MUST pass through
        :func:`gekko.audit.canonical.normalize_decimals` first (Pitfall 6).
    :param ts: Optional ISO-8601 timestamp string. Defaults to
        ``datetime.now(UTC).isoformat()``. Tests pin this for determinism
        checks; production callers omit it.
    :returns: The freshly inserted :class:`gekko.db.models.Event` row, with
        ``id``, ``prev_hash``, and ``row_hash`` populated.
    """
    lock = await _lock_for(user_id)
    async with lock:
        # 1. Read the previous row_hash for this user (or genesis).
        last_q = (
            select(Event.row_hash)
            .where(Event.user_id == user_id)
            .order_by(Event.id.desc())
            .limit(1)
        )
        prev_hash = (await session.execute(last_q)).scalar_one_or_none()
        if prev_hash is None:
            prev_hash = GENESIS_PREV_HASH

        # 2. Build the canonical subset (D-16 / RESEARCH §Pattern 3).
        resolved_ts = ts if ts is not None else datetime.now(UTC).isoformat()
        canonical = canonical_json(
            {
                "event_type": event_type,
                "payload": payload,
                "ts": resolved_ts,
                "user_id": user_id,
            }
        )

        # 3. Compute row_hash.
        row_hash = hashlib.sha256(
            prev_hash.encode("ascii") + canonical.encode("utf-8")
        ).hexdigest()

        # 4. Insert. payload_json is the FULL canonical-subset string so
        #    verify is a one-liner (Pattern 3 lock-in).
        row = Event(
            ts=resolved_ts,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type=event_type,
            payload_json=canonical,
            prev_hash=prev_hash,
            row_hash=row_hash,
        )
        session.add(row)
        await session.flush()
        return row


__all__: tuple[str, ...] = ("append_event",)
