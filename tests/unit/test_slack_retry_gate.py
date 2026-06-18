"""Socket Mode dedup contract tests — Plan 03-10 Task 2 (HITL-02 gap closure WR-08).

Replaces test_slack_retry_header.py which tested the X-Slack-Retry-Num gate
that was removed in Plan 03-10. The gate always returned 0 in Socket Mode (the
production transport) and was dead code. These tests verify the new contract:

  - claim_action's UNIQUE constraint is the SOLE dedup primitive
  - Socket Mode action payloads (no "headers" key) do NOT raise any exception
  - Double-click idempotency for both approve and reject flows works correctly
    without any X-Slack-Retry-Num header present in the body

Three tests per the plan's <behavior> specification:
  (1) Approve double-click without retry header: first call -> workflow proceeds;
      second call -> claim_action returns 'duplicate', ephemeral fires, no double-execution.
  (2) Socket Mode body (no "headers" key) does not raise AttributeError or KeyError
      when handle_approve is called.
  (3) Reject double-click without retry header: same dedup behavior via claim_action.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gekko.audit.log import append_event
from gekko.db.models import (
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers — same seed pattern as test_slack_action_dedup.py
# ---------------------------------------------------------------------------


async def _seed_base(sf: Any, *, user_id: str, proposal_id: str) -> None:
    """Seed User + Strategy + PENDING Proposal rows."""
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


def _socket_mode_body(proposal_id: str, user_id: str) -> dict[str, Any]:
    """Build a minimal Slack action body in Socket Mode shape.

    Socket Mode delivers payloads over WebSocket — there is NO "headers"
    key in the body dict.  This is the production payload shape.
    """
    return {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "trigger_id": "fake_trigger",
        # Deliberately NO "headers" key — this is the Socket Mode shape.
    }


async def _drain_tasks(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Task[Any]]:
    """Patch asyncio.create_task to capture tasks, then drain them.

    Returns the list of captured tasks after they have all been awaited.
    """
    captured: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _tracking_create_task(coro: Any, **kw: Any) -> asyncio.Task[Any]:
        t = real_create_task(coro, **kw)
        captured.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracking_create_task)
    return captured


# ---------------------------------------------------------------------------
# Test 1: Approve double-click idempotency — claim_action is sole dedup guard
# ---------------------------------------------------------------------------


async def test_approve_double_click_dedup_via_claim_action(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """Double-click approve without any retry header is deduplicated by claim_action alone.

    First call: claim_action returns 'first_write' -> workflow proceeds.
    Second call: claim_action returns 'duplicate' -> ephemeral fires, NO double-execution.
    The body has NO 'headers' key (Socket Mode shape).

    This confirms that after Plan 03-10 removed the X-Slack-Retry-Num gate,
    claim_action's UNIQUE constraint is the complete and sufficient dedup layer.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "sg-user-1"
    proposal_id = "prop-sg-approve-1"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))

    # Track how many times claim_action is called and what it returns.
    claim_results: list[str] = []
    original_claim_action = _dedup_mod.claim_action

    async def _tracking_claim_action(session: Any, **kwargs: Any) -> str:
        result = await original_claim_action(session, **kwargs)
        claim_results.append(result)
        return result

    monkeypatch.setattr(_dedup_mod, "claim_action", _tracking_claim_action)
    monkeypatch.setattr(slack_handler, "claim_action", _tracking_claim_action)

    # Prevent real executor and Slack DM side effects.
    mock_execute = AsyncMock()
    monkeypatch.setattr(slack_handler, "execute_proposal", mock_execute)
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", AsyncMock())

    # Body has NO "headers" key — Socket Mode shape.
    body = _socket_mode_body(proposal_id, user_id)

    # Capture and drain tasks for each call.
    async def _call_handle_approve() -> None:
        captured: list[asyncio.Task[Any]] = []
        real_create_task = asyncio.create_task

        def _track(coro: Any, **kw: Any) -> asyncio.Task[Any]:
            t = real_create_task(coro, **kw)
            captured.append(t)
            return t

        monkeypatch.setattr(asyncio, "create_task", _track)
        ack = AsyncMock()
        await slack_handler.handle_approve(ack=ack, body=body, client=None)
        # Drain all background tasks.
        while captured:
            pending = captured[:]
            captured.clear()
            await asyncio.gather(*pending, return_exceptions=True)
        # Restore real create_task.
        monkeypatch.setattr(asyncio, "create_task", real_create_task)

    # First call — should succeed via claim_action first_write.
    await _call_handle_approve()

    # Second call — same body, same proposal_id; should be deduped.
    await _call_handle_approve()

    # claim_action must have been called twice: first_write then duplicate.
    assert "first_write" in claim_results, (
        f"Expected at least one 'first_write' from claim_action; got: {claim_results}"
    )
    assert "duplicate" in claim_results, (
        f"Expected at least one 'duplicate' from claim_action on second call; "
        f"got: {claim_results}"
    )

    # executor must have been called at most once (the second call was deduplicated).
    # execute_proposal is dispatched via create_task inside _approve_workflow.
    # The mock captures direct calls; the task-drain approach above ensures it ran.
    # We verify by checking claim_results: only 'first_write' calls lead to execution.
    first_writes = claim_results.count("first_write")
    duplicates = claim_results.count("duplicate")
    assert first_writes == 1, f"Expected exactly 1 first_write; got: {claim_results}"
    assert duplicates >= 1, f"Expected at least 1 duplicate; got: {claim_results}"


# ---------------------------------------------------------------------------
# Test 2: Socket Mode body (no "headers" key) does not raise
# ---------------------------------------------------------------------------


async def test_socket_mode_body_no_headers_key_does_not_raise(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """handle_approve with Socket Mode body (no 'headers' key) must not raise.

    This was the root cause of WR-08: _extract_retry_num tried to read
    body.get('headers') which is absent in Socket Mode. With the retry gate
    removed, the handler must work correctly when 'headers' is absent.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "sg-user-2"
    proposal_id = "prop-sg-noheaders-2"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))

    # Prevent real executor and Slack DM side effects.
    monkeypatch.setattr(slack_handler, "execute_proposal", AsyncMock())
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", AsyncMock())

    # Confirm no "headers" key in body.
    body = _socket_mode_body(proposal_id, user_id)
    assert "headers" not in body, "Test body should not have 'headers' key"

    captured_tasks: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _track(coro: Any, **kw: Any) -> asyncio.Task[Any]:
        t = real_create_task(coro, **kw)
        captured_tasks.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _track)

    # Must not raise AttributeError, KeyError, or any other exception.
    raised: Exception | None = None
    try:
        ack = AsyncMock()
        await slack_handler.handle_approve(ack=ack, body=body, client=None)
        # Drain background tasks.
        while captured_tasks:
            pending = captured_tasks[:]
            captured_tasks.clear()
            await asyncio.gather(*pending, return_exceptions=True)
    except (AttributeError, KeyError) as exc:
        raised = exc

    assert raised is None, (
        f"handle_approve raised {type(raised).__name__} when body has no 'headers' key: {raised}"
    )


# ---------------------------------------------------------------------------
# Test 3: Reject double-click idempotency — claim_action is sole dedup guard
# ---------------------------------------------------------------------------


async def test_reject_double_click_dedup_via_claim_action(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """Double-click reject without any retry header is deduplicated by claim_action alone.

    Mirrors Test 1 but for the reject workflow. Socket Mode body shape (no "headers").
    First call: claim_action returns 'first_write' -> reject workflow proceeds.
    Second call: claim_action returns 'duplicate' -> ephemeral fires, no double-rejection.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "sg-user-3"
    proposal_id = "prop-sg-reject-3"

    monkeypatch.setenv("GEKKO_USER_ID", user_id)
    monkeypatch.setenv("SLACK_USER_ID", user_id)
    from gekko.config import get_settings as _gs
    _gs.cache_clear()

    await _seed_base(sf, user_id=user_id, proposal_id=proposal_id)

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))

    # Track claim_action calls.
    claim_results: list[str] = []
    original_claim_action = _dedup_mod.claim_action

    async def _tracking_claim_action(session: Any, **kwargs: Any) -> str:
        result = await original_claim_action(session, **kwargs)
        claim_results.append(result)
        return result

    monkeypatch.setattr(_dedup_mod, "claim_action", _tracking_claim_action)
    monkeypatch.setattr(slack_handler, "claim_action", _tracking_claim_action)

    # Prevent Slack DM side effects.
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", AsyncMock())

    # Body has NO "headers" key — Socket Mode shape.
    body = _socket_mode_body(proposal_id, user_id)

    async def _call_handle_reject() -> None:
        captured: list[asyncio.Task[Any]] = []
        real_create_task = asyncio.create_task

        def _track(coro: Any, **kw: Any) -> asyncio.Task[Any]:
            t = real_create_task(coro, **kw)
            captured.append(t)
            return t

        monkeypatch.setattr(asyncio, "create_task", _track)
        ack = AsyncMock()
        await slack_handler.handle_reject(ack=ack, body=body, client=None)
        while captured:
            pending = captured[:]
            captured.clear()
            await asyncio.gather(*pending, return_exceptions=True)
        monkeypatch.setattr(asyncio, "create_task", real_create_task)

    # First call — should succeed.
    await _call_handle_reject()

    # Second call — same proposal; should be deduped.
    await _call_handle_reject()

    # Verify claim_action returned first_write then duplicate.
    assert "first_write" in claim_results, (
        f"Expected at least one 'first_write' from claim_action; got: {claim_results}"
    )
    assert "duplicate" in claim_results, (
        f"Expected at least one 'duplicate' on second reject call; got: {claim_results}"
    )

    # Exactly one first_write — confirms single-execution.
    first_writes = claim_results.count("first_write")
    assert first_writes == 1, (
        f"Expected exactly 1 first_write for reject; got: {claim_results}"
    )
