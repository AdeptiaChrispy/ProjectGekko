"""Audit hash chain integrity verifier — Plan 01-04 Task 3.

:func:`walk_chain` is the read-only counterpart to
:func:`gekko.audit.log.append_event`. It walks a user's events in id order
and recomputes the SHA-256 chain link by link. Any row whose stored
``row_hash`` does not match the recomputation — or whose ``prev_hash`` does
not match the previous row's ``row_hash`` (or :data:`GENESIS_PREV_HASH` for
the first row) — is appended to the returned ``breaks`` list.

The verification is intentionally **side-effect-free**: no row mutation, no
commit, no exception raised. Callers (``gekko audit verify`` CLI in Plan
01-09, the startup check in the executor lifecycle, etc.) decide what to do
with the broken-rows list — typically log structured ``audit_chain_broken``
events and surface a CLI exit code.

**Why ``payload_json`` is the canonical-subset string** (RESEARCH §Pattern 3
lock-in, enforced by :func:`gekko.audit.log.append_event`):
re-hashing here is one line — ``sha256(prev_hash + row.payload_json)`` —
no JSON re-parsing, no order assumption, no chance of locale drift.

**Per-user scope** (D-21): only ``WHERE user_id = :uid`` events are walked.
alice's tampered events are invisible to ``walk_chain(session, user_id="bob")``.

References:
  * CONTEXT.md D-14 / D-16 — events table + hash chain
  * CONTEXT.md D-21 — user-scoped data model
  * RESEARCH.md §"Pattern 3" — payload_json IS the canonical subset string
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.canonical import GENESIS_PREV_HASH
from gekko.db.models import Event


async def walk_chain(session: AsyncSession, user_id: str) -> list[int]:
    """Return the ids of rows whose hash chain is broken; ``[]`` if intact.

    Walks every event for ``user_id`` ordered by ``id`` ascending. For each
    row:

    1. Recompute ``sha256(expected_prev.encode("ascii") +
       row.payload_json.encode("utf-8")).hexdigest()``.
    2. If the recomputation does not equal ``row.row_hash``, OR
       ``row.prev_hash`` does not equal ``expected_prev``, append
       ``row.id`` to the breaks list.
    3. Advance ``expected_prev`` to ``row.row_hash`` (so subsequent rows are
       still checked even if an earlier one is broken — useful for
       identifying ALL broken rows, not just the first).

    The function is async (uses an ``AsyncSession``) but does not yield
    control after the initial SELECT — the per-row hash work is pure CPU.

    :param session: Async session bound to the per-user SQLCipher engine.
    :param user_id: The user whose chain to verify. Only their events are
        inspected.
    :returns: List of ``Event.id`` values for each broken row. Empty list
        means the chain is fully intact.
    """
    q = (
        select(Event)
        .where(Event.user_id == user_id)
        .order_by(Event.id.asc())
    )
    rows = list((await session.execute(q)).scalars().all())

    breaks: list[int] = []
    expected_prev: str = GENESIS_PREV_HASH
    for row in rows:
        recomputed = hashlib.sha256(
            expected_prev.encode("ascii")
            + row.payload_json.encode("utf-8")
        ).hexdigest()
        if row.row_hash != recomputed or row.prev_hash != expected_prev:
            breaks.append(row.id)
        # Advance the walker even on a break — we want ALL inconsistent
        # rows surfaced, not just the first. Using row.row_hash (the
        # STORED value, not the recomputed one) means the next row's
        # prev_hash check is against what the writer claimed, which is
        # the most useful signal for forensic analysis.
        expected_prev = row.row_hash

    return breaks


__all__: tuple[str, ...] = ("walk_chain",)
