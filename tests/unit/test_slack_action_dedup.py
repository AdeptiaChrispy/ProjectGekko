"""Tests for the ``claim_action`` dedup helper — Plan 03-02 Task 1.

Six test cases per the plan's <behavior> specification:
  (a) first-write returns 'first_write' + row visible via select(SlackActionDedup)
  (b) second identical INSERT returns 'duplicate' without raising
  (c) audit log contains exactly one dedup_click event after a duplicate
  (d) cross-actor (different actor_slack_user_id) INSERTs both succeed as
      'first_write' per D-42
  (e) cross-surface different-source ('slack' vs 'dashboard') on the same
      (proposal_id, action_id, actor_gekko_user_id) produce two rows per D-56
  (f) slack_trigger_id is persisted on the row + masked from __repr__
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.log import append_event
from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    SlackActionDedup,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_base(sf: Any, *, user_id: str, proposal_id: str) -> None:
    """Seed the minimum rows needed for SlackActionDedup FK constraints.

    SlackActionDedup has:
      - proposal_id FK -> proposals.proposal_id
      - actor_gekko_user_id FK -> users.user_id
    We need at least a User and a Proposal row seeded.
    """
    now = datetime.now(UTC).isoformat()
    strategy_id = f"strat-{proposal_id}"
    async with sf() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="test-strategy",
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status="PENDING",
                payload_json="{}",
                client_order_id=None,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )
        # Seed the genesis audit event so the chain starts clean.
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="proposal",
            payload={"proposal_id": proposal_id},
        )


# ---------------------------------------------------------------------------
# (a) first-write returns 'first_write' + row visible in DB
# ---------------------------------------------------------------------------


async def test_first_click_first_write(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claim_action returns 'first_write' on first INSERT + row persists."""
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-a"
    proposal_id = "prop-fw-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    # Patch the session-factory shim so dedup's fresh-session path stays in-memory.
    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    async with sf() as session, session.begin():
        outcome = await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="approve_proposal",
            actor_slack_user_id="U_ALICE",
            actor_gekko_user_id=user_id,
            source="slack",
        )

    assert outcome == "first_write"

    # Verify the row is visible in the DB.
    async with sf() as session:
        rows = (await session.execute(select(SlackActionDedup))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.proposal_id == proposal_id
    assert row.action_id == "approve_proposal"
    assert row.actor_slack_user_id == "U_ALICE"
    assert row.actor_gekko_user_id == user_id
    assert row.source == "slack"
    assert row.result == "first_write"


# ---------------------------------------------------------------------------
# (b) second identical INSERT returns 'duplicate' without raising
# ---------------------------------------------------------------------------


async def test_second_click_duplicate(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second call with same (proposal_id, action_id, actor_slack_user_id) returns 'duplicate'."""
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-b"
    proposal_id = "prop-dup-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    call_kwargs = dict(
        proposal_id=proposal_id,
        action_id="approve_proposal",
        actor_slack_user_id="U_BOB",
        actor_gekko_user_id=user_id,
        source="slack",
    )

    # First call — should succeed.
    async with sf() as session, session.begin():
        first = await _dedup_mod.claim_action(session, **call_kwargs)
    assert first == "first_write"

    # Second call — same kwargs; must return 'duplicate' without raising.
    async with sf() as session, session.begin():
        second = await _dedup_mod.claim_action(session, **call_kwargs)
    assert second == "duplicate"

    # Only ONE dedup row should exist (the first_write; duplicate path does NOT
    # insert a new row — it returns the sentinel).
    async with sf() as session:
        rows = (await session.execute(select(SlackActionDedup))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# (c) audit log contains exactly one dedup_click event after a duplicate
# ---------------------------------------------------------------------------


async def test_dedup_click_event_appended(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dedup_click event is appended to the audit log on the duplicate path."""
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-c"
    proposal_id = "prop-evt-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    call_kwargs = dict(
        proposal_id=proposal_id,
        action_id="approve_proposal",
        actor_slack_user_id="U_CAROL",
        actor_gekko_user_id=user_id,
        source="slack",
    )

    async with sf() as session, session.begin():
        await _dedup_mod.claim_action(session, **call_kwargs)

    async with sf() as session, session.begin():
        await _dedup_mod.claim_action(session, **call_kwargs)

    # Exactly ONE dedup_click event should be in the audit log.
    async with sf() as session:
        events = (await session.execute(select(Event))).scalars().all()
    dedup_events = [e for e in events if e.event_type == "dedup_click"]
    assert len(dedup_events) == 1, (
        f"Expected exactly 1 dedup_click event; got {[e.event_type for e in events]}"
    )


# ---------------------------------------------------------------------------
# (d) cross-actor both succeed as 'first_write' per D-42
# ---------------------------------------------------------------------------


async def test_cross_actor_both_first_write(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different actor_slack_user_id values on different action_ids both get first_write (D-42).

    D-42 scenario: User A approves (action_id='approve_proposal'); a different
    Slack user fires Reject (action_id='reject_proposal') on the same proposal.
    Both register distinct dedup rows.

    The uq_dedup_slack UNIQUE is on (proposal_id, action_id, actor_slack_user_id)
    — different actors on different action_ids are trivially distinct.
    The uq_dedup_dashboard UNIQUE is on (proposal_id, action_id,
    actor_gekko_user_id, source) — different action_ids make these distinct too.

    Note: two different Slack actors clicking the SAME action on the SAME
    proposal WOULD conflict on uq_dedup_dashboard (they share actor_gekko_user_id
    + source).  The real cross-user defense happens at the handler level via the
    configured slack_user_id check — by design, only one Slack user should be
    clicking each action in the single-operator model.
    """
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-d"
    proposal_id = "prop-ca-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    # Actor 1 clicks approve.
    async with sf() as session, session.begin():
        result1 = await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="approve_proposal",
            actor_slack_user_id="U_DAVE",
            actor_gekko_user_id=user_id,
            source="slack",
        )
    # A different Slack actor clicks reject (different action_id) for same proposal.
    async with sf() as session, session.begin():
        result2 = await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="reject_proposal",
            actor_slack_user_id="U_EVE",
            actor_gekko_user_id=user_id,
            source="slack",
        )

    assert result1 == "first_write"
    assert result2 == "first_write"

    # Two distinct dedup rows should exist.
    async with sf() as session:
        rows = (await session.execute(select(SlackActionDedup))).scalars().all()
    assert len(rows) == 2
    action_ids = {r.action_id for r in rows}
    assert action_ids == {"approve_proposal", "reject_proposal"}


# ---------------------------------------------------------------------------
# (e) cross-surface different-source produces two rows per D-56
# ---------------------------------------------------------------------------


async def test_cross_surface_both_first_write(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source='slack' and source='dashboard' each get their own dedup row (D-56).

    The uq_dedup_dashboard UNIQUE on (proposal_id, action_id,
    actor_gekko_user_id, source) means the same gekko user can approve via
    Slack and then dashboard (or vice versa) — each surface records its own
    first_write row. Only the same surface + same gekko_user is a duplicate.
    """
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-e"
    proposal_id = "prop-cs-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    # Slack surface click.
    async with sf() as session, session.begin():
        slack_result = await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="approve_proposal",
            actor_slack_user_id="U_FRANK",
            actor_gekko_user_id=user_id,
            source="slack",
        )
    # Dashboard surface click (same gekko user, different source).
    async with sf() as session, session.begin():
        dashboard_result = await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="approve_proposal",
            actor_slack_user_id=None,  # dashboard has no Slack actor id
            actor_gekko_user_id=user_id,
            source="dashboard",
        )

    assert slack_result == "first_write"
    assert dashboard_result == "first_write"

    async with sf() as session:
        rows = (await session.execute(select(SlackActionDedup))).scalars().all()
    assert len(rows) == 2
    sources = {r.source for r in rows}
    assert sources == {"slack", "dashboard"}


# ---------------------------------------------------------------------------
# (f) slack_trigger_id is persisted on the row + masked from __repr__
# ---------------------------------------------------------------------------


async def test_trigger_id_persisted_and_masked(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """slack_trigger_id stored in DB but excluded from __repr__ (T-03-01-03)."""
    from gekko.approval import dedup as _dedup_mod

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "user-f"
    proposal_id = "prop-tid-001"
    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _uid: (sf, None)
    )

    trigger_id = "t-secret-12345"

    async with sf() as session, session.begin():
        await _dedup_mod.claim_action(
            session,
            proposal_id=proposal_id,
            action_id="reject_proposal",
            actor_slack_user_id="U_GRACE",
            actor_gekko_user_id=user_id,
            source="slack",
            slack_trigger_id=trigger_id,
        )

    async with sf() as session:
        row = (
            await session.execute(select(SlackActionDedup))
        ).scalars().first()

    assert row is not None
    # Value persisted correctly.
    assert row.slack_trigger_id == trigger_id
    # Value absent from __repr__.
    r = repr(row)
    assert trigger_id not in r, f"trigger_id leaked in repr: {r}"
