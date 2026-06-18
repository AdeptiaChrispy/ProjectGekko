"""X-Slack-Retry-Num gate tests — SUPERSEDED by test_slack_retry_gate.py (Plan 03-10).

The X-Slack-Retry-Num HTTP header gate was removed in Plan 03-10 (WR-08 gap
closure). The gate read body['headers']['x-slack-retry-num'] which is absent in
Socket Mode (the production transport). It always returned 0 and was dead code.

Exactly-once execution is provided solely by claim_action's UNIQUE constraint
in dedup.py. See test_slack_retry_gate.py for the updated contract tests.

The tests that remain here validate behavior that is still correct after the
removal:
  (a) retry_num=0 header present -> claim_action is called (still works because
      the body shape with 'headers' is now just ignored — the handler goes
      straight to the background workflow which calls claim_action)

The test (b) that asserted the retry gate SHORT-CIRCUITS claim_action when
retry_num >= 1 is REMOVED — that gate no longer exists. claim_action is now
the sole guard and handles duplicate calls via UNIQUE constraint.

The test (c) that asserted retry_num >= 1 without a prior row falls through is
REMOVED — same reason. The handler now always dispatches the background
workflow regardless of retry headers; claim_action handles all dedup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gekko.db.models import (
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory
from gekko.audit.log import append_event

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# (a) Body with 'headers' key present still works — handler ignores headers now
# ---------------------------------------------------------------------------


async def test_body_with_headers_key_still_works(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: Any,
) -> None:
    """A body dict that happens to have a 'headers' key does not break handle_approve.

    After Plan 03-10 removed the retry gate, the handler ignores any 'headers'
    key in the body. This test confirms backward compatibility with bodies that
    include HTTP-style headers (e.g., from test harnesses or HTTP-mode Slack).
    claim_action is still called regardless of headers content.
    """
    from gekko.approval import dedup as _dedup_mod
    from gekko.approval import slack_handler

    sf = make_session_factory(temp_sqlcipher_db)
    user_id = "test-user"
    proposal_id = "prop-retry-compat"

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
    monkeypatch.setattr(slack_handler, "execute_proposal", AsyncMock())
    from gekko.execution import executor as _exec
    monkeypatch.setattr(_exec, "_send_slack_dm", AsyncMock())

    claim_called = False
    original_claim_action = _dedup_mod.claim_action

    async def _tracking_claim(session: Any, **kwargs: Any) -> str:
        nonlocal claim_called
        claim_called = True
        return await original_claim_action(session, **kwargs)

    monkeypatch.setattr(_dedup_mod, "claim_action", _tracking_claim)
    monkeypatch.setattr(slack_handler, "claim_action", _tracking_claim)

    # Body WITH 'headers' key — e.g., from HTTP-mode or test harness.
    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": user_id},
        "headers": {"x-slack-retry-num": "0"},
    }

    import asyncio
    tasks_captured: list[Any] = []
    real_create_task = asyncio.create_task

    def _tracked(coro: Any, **kw: Any) -> Any:
        t = real_create_task(coro, **kw)
        tasks_captured.append(t)
        return t

    monkeypatch.setattr(asyncio, "create_task", _tracked)
    ack = AsyncMock()
    await slack_handler.handle_approve(ack=ack, body=body, client=None)
    while tasks_captured:
        pending = tasks_captured[:]
        tasks_captured.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    # claim_action must have been called — the handler no longer checks headers.
    assert claim_called, "claim_action should be called regardless of headers content"
