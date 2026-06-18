"""Proposal state machine EXPIRED edge tests — Plan 03-01 Task 3.

Covers:
(a) PENDING -> EXPIRED valid via transition_status
(b) Re-entering EXPIRED is idempotent no-op (existing line 139-141 path)
(c) APPROVED -> EXPIRED raises ValueError
(d) expire_proposal convenience writes the audit event with D-50 payload shape
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.approval.proposals import STATE_TRANSITIONS, expire_proposal, transition_status
from gekko.db.models import (
    Event,
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed(
    session_factory: Any,
    *,
    status: str = "PENDING",
    user_id: str = "test-sm-user",
) -> tuple[str, str, str]:
    """Seed User + Strategy + Proposal; return (user_id, strategy_id, proposal_id)."""
    strategy_id = "strat-sm-" + uuid4().hex
    proposal_id = uuid4().hex
    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="test-sm-strategy",
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
                status=status,
                payload_json="{}",
                client_order_id=None,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )
    return user_id, strategy_id, proposal_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pending_to_expired_in_state_transitions() -> None:
    """PENDING -> EXPIRED must be present in STATE_TRANSITIONS (D-50)."""
    assert ("PENDING", "EXPIRED") in STATE_TRANSITIONS


@pytest.mark.asyncio
async def test_pending_to_expired_valid(temp_sqlcipher_db: Any) -> None:
    """transition_status PENDING -> EXPIRED succeeds and persists the status."""
    sf = make_session_factory(temp_sqlcipher_db)
    user_id, _, proposal_id = await _seed(sf, status="PENDING")

    async with sf() as session, session.begin():
        row = await transition_status(
            session,
            proposal_id,
            from_status="PENDING",
            to_status="EXPIRED",
        )
        assert row.status == "EXPIRED"

    # Confirm persisted
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "EXPIRED"


@pytest.mark.asyncio
async def test_expired_to_expired_idempotent(temp_sqlcipher_db: Any) -> None:
    """transition_status EXPIRED -> EXPIRED is a no-op (idempotence — lines 139-141)."""
    sf = make_session_factory(temp_sqlcipher_db)
    _, _, proposal_id = await _seed(sf, status="EXPIRED")

    async with sf() as session, session.begin():
        row = await transition_status(
            session,
            proposal_id,
            from_status="EXPIRED",
            to_status="EXPIRED",
        )
        # Must not raise; status unchanged
        assert row.status == "EXPIRED"


@pytest.mark.asyncio
async def test_approved_to_expired_raises(temp_sqlcipher_db: Any) -> None:
    """transition_status APPROVED -> EXPIRED raises ValueError (not allowed)."""
    sf = make_session_factory(temp_sqlcipher_db)
    _, _, proposal_id = await _seed(sf, status="APPROVED")

    with pytest.raises(ValueError, match="Invalid proposal status transition"):
        async with sf() as session, session.begin():
            await transition_status(
                session,
                proposal_id,
                from_status="APPROVED",
                to_status="EXPIRED",
            )


@pytest.mark.asyncio
async def test_expire_proposal_helper_audit_event(temp_sqlcipher_db: Any) -> None:
    """expire_proposal transitions row to EXPIRED and writes D-50 audit event."""
    sf = make_session_factory(temp_sqlcipher_db)
    user_id, strategy_id, proposal_id = await _seed(sf, status="PENDING")
    expired_at = datetime.now(UTC).isoformat()

    async with sf() as session, session.begin():
        row = await expire_proposal(
            session,
            proposal_id,
            reason="timeout",
            expired_at=expired_at,
            configured_timeout_minutes=30,
        )
        assert row.status == "EXPIRED"

    # Verify audit event
    async with sf() as session:
        import json

        events_q = (
            await session.execute(
                select(Event).where(
                    Event.user_id == user_id,
                    Event.strategy_id == strategy_id,
                    Event.event_type == "expiration",
                )
            )
        ).scalars().all()
        assert len(events_q) == 1
        # payload_json contains the canonical-subset string; parse nested payload
        canonical = json.loads(events_q[0].payload_json)
        payload = canonical["payload"]
        assert payload["proposal_id"] == proposal_id
        assert payload["reason"] == "timeout"
        assert payload["expired_at"] == expired_at
        assert payload["configured_timeout_minutes"] == 30
