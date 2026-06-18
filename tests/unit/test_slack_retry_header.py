"""Tests for X-Slack-Retry-Num header gating — Plan 03-02 Task 2.

Three cases per the plan's <behavior> specification:
  (a) retry_num=0 -> claim_action called (first delivery or zero-retry)
  (b) retry_num=1 + dedup row exists -> claim_action NOT called (short-circuit)
  (c) retry_num=1 + dedup row absent -> claim_action called (defensive: a retry
      without a prior write is treated as a first delivery)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.db.models import (
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
    """Seed User + Strategy + PENDING Proposal rows."""
    from gekko.audit.log import append_event

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
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="proposal",
            payload={"proposal_id": proposal_id},
        )


async def _seed_dedup_row(sf: Any, *, user_id: str, proposal_id: str) -> None:
    """Seed a 'first_write' dedup row so the retry check finds an existing row."""
    now = datetime.now(UTC).isoformat()
    async with sf() as session, session.begin():
        session.add(
            SlackActionDedup(
                proposal_id=proposal_id,
                action_id="approve_proposal",
                actor_slack_user_id=user_id,
                actor_gekko_user_id=user_id,
                source="slack",
                slack_trigger_id=None,
                inserted_at=now,
                result="first_write",
            )
        )


# ---------------------------------------------------------------------------
# (a) retry_num=0 -> claim_action called (normal first delivery)
# ---------------------------------------------------------------------------


async def test_retry_num_zero_passes_through(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """When x-slack-retry-num header is '0', handle_approve calls claim_action."""
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"
    proposal_id = "prop-retry-a"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # Prevent the approve workflow from touching the real executor or Slack.
    monkeypatch.setattr(
        slack_handler, "execute_proposal", AsyncMock()
    )
    async def _noop_send(uid: str, msg: str) -> None:
        pass
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", _noop_send)

    claim_call_count = 0
    original_claim_action = _dedup_mod.claim_action

    async def _counting_claim(session: Any, **kwargs: Any) -> str:
        nonlocal claim_call_count
        claim_call_count += 1
        return await original_claim_action(session, **kwargs)

    monkeypatch.setattr(_dedup_mod, "claim_action", _counting_claim)
    monkeypatch.setattr(slack_handler, "claim_action", _counting_claim)

    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        # retry_num=0 — normal first delivery
        "headers": {"x-slack-retry-num": "0"},
    }
    ack = AsyncMock()
    await slack_handler.handle_approve(ack=ack, body=body, client=None)

    # Drain the background task.
    import asyncio
    tasks: list[Any] = []
    real_create_task = asyncio.create_task

    # Re-run with task capture to drain.
    claim_call_count = 0
    tasks_captured: list[Any] = []
    real_create_task2 = asyncio.create_task

    def _tracked(coro: Any, **kw: Any) -> Any:
        t = real_create_task2(coro, **kw)
        tasks_captured.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked)
    ack2 = AsyncMock()
    await slack_handler.handle_approve(ack=ack2, body=body, client=None)
    while tasks_captured:
        pending = tasks_captured[:]
        tasks_captured.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    # claim_action must have been called at least once.
    assert claim_call_count >= 1, "claim_action should be called for retry_num=0"


# ---------------------------------------------------------------------------
# (b) retry_num=1 + dedup row exists -> short-circuit (claim_action NOT called)
# ---------------------------------------------------------------------------


async def test_retry_header_suppresses_duplicate_claim(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """When x-slack-retry-num >= 1 AND a dedup row already exists, skip claim."""
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"
    proposal_id = "prop-retry-b"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)
    # Seed the dedup row to simulate a prior first_write.
    await _seed_dedup_row(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    claim_called = False

    async def _spy_claim(session: Any, **kwargs: Any) -> str:
        nonlocal claim_called
        claim_called = True
        return "first_write"

    monkeypatch.setattr(slack_handler, "claim_action", _spy_claim)

    import asyncio
    tasks_captured: list[Any] = []
    real_create_task = asyncio.create_task

    def _tracked(coro: Any, **kw: Any) -> Any:
        t = real_create_task(coro, **kw)
        tasks_captured.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked)

    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        # Slack retry — retry_num=1 with a prior dedup row present.
        "headers": {"x-slack-retry-num": "1"},
    }
    ack = AsyncMock()
    await slack_handler.handle_approve(ack=ack, body=body, client=None)
    while tasks_captured:
        pending = tasks_captured[:]
        tasks_captured.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    # claim_action must NOT have been called — the retry gate short-circuited.
    assert not claim_called, (
        "claim_action should NOT be called when retry_num >= 1 and a dedup row exists"
    )


# ---------------------------------------------------------------------------
# (c) retry_num=1 + dedup row absent -> claim_action called (defensive)
# ---------------------------------------------------------------------------


async def test_retry_header_no_prior_row_passes_through(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """When retry_num >= 1 but NO dedup row exists, treat as first delivery."""
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"
    proposal_id = "prop-retry-c"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)
    # NO dedup row seeded — simulates a retry where no prior write landed.

    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(
        _dedup_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    # Prevent executor + Slack DM side effects.
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", AsyncMock())
    monkeypatch.setattr(slack_handler, "execute_proposal", AsyncMock())

    claim_called = False

    original_claim = _dedup_mod.claim_action

    async def _spy_claim(session: Any, **kwargs: Any) -> str:
        nonlocal claim_called
        claim_called = True
        return await original_claim(session, **kwargs)

    monkeypatch.setattr(_dedup_mod, "claim_action", _spy_claim)
    monkeypatch.setattr(slack_handler, "claim_action", _spy_claim)

    import asyncio
    tasks_captured: list[Any] = []
    real_create_task = asyncio.create_task

    def _tracked(coro: Any, **kw: Any) -> Any:
        t = real_create_task(coro, **kw)
        tasks_captured.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked)

    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        # retry_num=1 but NO prior dedup row — defensive first delivery.
        "headers": {"x-slack-retry-num": "1"},
    }
    ack = AsyncMock()
    await slack_handler.handle_approve(ack=ack, body=body, client=None)
    while tasks_captured:
        pending = tasks_captured[:]
        tasks_captured.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    assert claim_called, (
        "claim_action should be called when retry_num >= 1 but no dedup row exists"
    )
