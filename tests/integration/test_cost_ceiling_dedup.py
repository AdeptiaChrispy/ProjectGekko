"""Real-session regression test: cost_ceiling dedup persistence — Plan 04-08 Task 2.

Proves that ``check_cost_ceiling`` commits ``cost_alert_80_sent_date`` /
``cost_alert_100_sent_date`` to the DB so a second call in the same day
returns ``just_crossed_80=False`` / ``just_crossed_100=False``.

These tests MUST:
  - fail against the pre-fix flush-only code (sent-date discarded on rollback)
  - pass after the ``session.begin()`` commit fix

Design:
  - Real SQLCipher engine (temp_sqlcipher_db fixture + make_session_factory)
  - No mocking of the session or User ORM
  - llm_cost events seeded via append_event (canonical-wrapped) so
    check_cost_ceiling reads them from the real DB via the real query
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.agent.cost_ceiling import check_cost_ceiling
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.db.models import User
from gekko.db.session import make_session_factory

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared seed helper
# ---------------------------------------------------------------------------


async def _seed_user_with_spend(
    engine: Any,
    user_id: str,
    cost_usd_str: str,
    ceiling_str: str,
) -> None:
    """Seed a User row + one llm_cost event at the given cost."""
    now_iso = datetime.now(UTC).isoformat()
    async with AsyncSession(engine) as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=now_iso,
                timezone="America/New_York",
                daily_cost_ceiling_usd=ceiling_str,
                cost_alert_80_sent_date=None,
                cost_alert_100_sent_date=None,
            )
        )
        await session.flush()
        await append_event(
            session,
            user_id=user_id,
            strategy_id=None,
            event_type="llm_cost",
            payload=normalize_decimals(
                {"cost_usd": cost_usd_str, "call_type": "researcher"}
            ),
        )


# ---------------------------------------------------------------------------
# Test 1 — 80% dedup persists across sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_80_persists_across_sessions(temp_sqlcipher_db: Any) -> None:
    """Two calls to check_cost_ceiling in degrade territory (≥80% / <100%).

    Call 1: just_crossed_80 must be True (first crossing).
    Call 2: just_crossed_80 must be False (dedup committed in call 1).
    """
    uid = uuid4().hex[:8]
    # 90% spend: 0.09 / 0.10 = 90% → degrade action
    await _seed_user_with_spend(temp_sqlcipher_db, uid, "0.09", "0.10")
    sf = make_session_factory(temp_sqlcipher_db)

    # --- CALL 1: first threshold crossing ---
    r1 = await check_cost_ceiling(session_factory=sf, user_id=uid)
    assert r1.action == "degrade", (
        f"Expected action='degrade' at 90%, got {r1.action!r}"
    )
    assert r1.just_crossed_80 is True, (
        "Call 1: just_crossed_80 must be True on first threshold crossing"
    )

    # --- VERIFY PERSISTENCE: fresh read session must show sent-date committed ---
    async with AsyncSession(temp_sqlcipher_db) as read_session:
        user_row = await read_session.get(User, uid)
    assert user_row is not None
    assert user_row.cost_alert_80_sent_date is not None, (
        "cost_alert_80_sent_date must be persisted to the DB after call 1 "
        "(pre-fix flush-only code rolls back this write — this assertion "
        "confirms the session.begin() commit is in place)"
    )

    # --- CALL 2: same day, same engine — dedup must fire ---
    r2 = await check_cost_ceiling(session_factory=sf, user_id=uid)
    assert r2.just_crossed_80 is False, (
        "Call 2: just_crossed_80 must be False because the sent-date was "
        "committed in call 1. Pre-fix this fails because the flush-only "
        "code discards the UPDATE on session-exit."
    )


# ---------------------------------------------------------------------------
# Test 2 — 100% dedup persists across sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_100_persists_across_sessions(temp_sqlcipher_db: Any) -> None:
    """Two calls to check_cost_ceiling in halt territory (100% spend).

    Call 1: just_crossed_80=True AND just_crossed_100=True (both first crossings).
    Call 2: just_crossed_80=False AND just_crossed_100=False (both deduped).
    """
    uid = uuid4().hex[:8]
    # 100% spend: 0.10 / 0.10 = 100% → halt action
    await _seed_user_with_spend(temp_sqlcipher_db, uid, "0.10", "0.10")
    sf = make_session_factory(temp_sqlcipher_db)

    # --- CALL 1: first threshold crossing ---
    r1 = await check_cost_ceiling(session_factory=sf, user_id=uid)
    assert r1.action == "halt", (
        f"Expected action='halt' at 100%, got {r1.action!r}"
    )
    assert r1.just_crossed_80 is True, (
        "Call 1: just_crossed_80 must be True on first halt crossing (80% path "
        "fires whenever action is 'halt' and sent-date has not been set today)"
    )
    assert r1.just_crossed_100 is True, (
        "Call 1: just_crossed_100 must be True on first halt crossing"
    )

    # --- VERIFY PERSISTENCE: fresh read session must show both sent-dates committed ---
    async with AsyncSession(temp_sqlcipher_db) as read_session:
        user_row = await read_session.get(User, uid)
    assert user_row is not None
    assert user_row.cost_alert_80_sent_date is not None, (
        "cost_alert_80_sent_date must be persisted after call 1"
    )
    assert user_row.cost_alert_100_sent_date is not None, (
        "cost_alert_100_sent_date must be persisted after call 1"
    )

    # --- CALL 2: same day, same engine — both dedup gates must fire ---
    r2 = await check_cost_ceiling(session_factory=sf, user_id=uid)
    assert r2.just_crossed_80 is False, (
        "Call 2: just_crossed_80 must be False (deduped from call 1)"
    )
    assert r2.just_crossed_100 is False, (
        "Call 2: just_crossed_100 must be False (deduped from call 1)"
    )
