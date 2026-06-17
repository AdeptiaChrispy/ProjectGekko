"""Tests for the Slack approval + proposal state machine — Plan 01-08 Task 3.

Covers three modules:

* ``gekko.approval.proposals`` — state machine (PENDING -> APPROVED ->
  EXECUTING -> FILLED). transition_status is the atomic primitive every
  approval / executor path goes through.
* ``gekko.slack.commands`` — ``/gekko run <strategy>`` slash command (D-06).
* ``gekko.approval.slack_handler`` — approve / reject / edit-size-stub /
  escalate-stub action handlers (HITL-04).

Per the plan, ack() MUST be the first awaited call in every handler
(RESEARCH Pitfall 3 — 3-second Slack deadline). The tests verify this by
introspecting the call order on AsyncMock ack().
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from gekko.approval.proposals import (
    STATE_TRANSITIONS,
    approve_proposal,
    reject_proposal,
    transition_status,
)
from gekko.db.models import Event, Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal() -> TradeProposal:
    return TradeProposal(
        user_id="test-user",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        # Plan 02-01 Task 3: target_notional_usd (D-27) + account_mode (BLOCKER #5).
        target_notional_usd=Decimal("6172.80"),
        order_type="limit",
        limit_price=Decimal("1234.56"),
        rationale="Bullish on AI infra leaders.",
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at=datetime.now(UTC).isoformat(),
                summary="NVDA last trade $1,234.56",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at=datetime.now(UTC).isoformat(),
                summary="NVDA earnings beat by 12%.",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at=datetime.now(UTC).isoformat(),
                summary="10-Q filed.",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="Consider AMD",
                why_rejected="Lower data-center exposure.",
            ),
        ],
        client_order_id="a" * 32,
        account_mode="PAPER",
    )


async def _seed_user_and_strategy(
    session_factory: Any, *, user_id: str = "test-user"
) -> str:
    """Create a User + Strategy row; return the strategy_id.

    The intermediate ``session.flush()`` is required: SQLAlchemy 2.x does
    not auto-order inserts by FK dependency unless a ``relationship()`` is
    declared on the parent (it isn't — see ``gekko.db.models``). Without
    the flush the Strategy INSERT runs first and SQLCipher's
    ``PRAGMA foreign_keys = ON`` rejects the row.
    """
    strategy_id = "strat-" + uuid4().hex
    async with session_factory() as session, session.begin():
        session.add(
            User(
                user_id=user_id,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="ai-infra-bull",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    return strategy_id


async def _seed_pending_proposal(
    session_factory: Any,
    *,
    proposal_id: str,
    user_id: str,
    strategy_id: str,
    payload: TradeProposal | None = None,
) -> None:
    """Persist a PENDING Proposal row for the approval-path tests."""
    p = payload or _make_trade_proposal()
    p = p.model_copy(update={"user_id": user_id, "decision_id": proposal_id})
    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status="PENDING",
                payload_json=p.model_dump_json(),
                client_order_id=p.client_order_id,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
            )
        )


# ---------------------------------------------------------------------------
# Behaviors — proposals.py (state machine)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_pending_to_approved(temp_sqlcipher_db: Any) -> None:
    """transition_status PENDING -> APPROVED updates the row when from matches."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf)
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )
    async with sf() as session, session.begin():
        row = await transition_status(
            session, proposal_id, from_status="PENDING", to_status="APPROVED"
        )
    assert row is not None
    assert row.status == "APPROVED"


@pytest.mark.asyncio
async def test_transition_idempotent_when_already_in_target_status(
    temp_sqlcipher_db: Any,
) -> None:
    """transition_status APPROVED -> APPROVED is a no-op (returns the row)."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf)
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )
    async with sf() as session, session.begin():
        await transition_status(
            session, proposal_id, from_status="PENDING", to_status="APPROVED"
        )
    async with sf() as session, session.begin():
        row = await transition_status(
            session, proposal_id, from_status="APPROVED", to_status="APPROVED"
        )
    assert row.status == "APPROVED"


@pytest.mark.asyncio
async def test_transition_rejects_invalid_backward_move(
    temp_sqlcipher_db: Any,
) -> None:
    """APPROVED -> PENDING is not in STATE_TRANSITIONS and must raise."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf)
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )
    async with sf() as session, session.begin():
        await transition_status(
            session, proposal_id, from_status="PENDING", to_status="APPROVED"
        )
    async with sf() as session, session.begin():
        with pytest.raises(ValueError):
            await transition_status(
                session,
                proposal_id,
                from_status="APPROVED",
                to_status="PENDING",
            )


@pytest.mark.asyncio
async def test_state_transitions_table_covers_phase1_lifecycle() -> None:
    """STATE_TRANSITIONS must include the P1 lifecycle paths."""
    expected = {
        ("PENDING", "APPROVED"),
        ("PENDING", "REJECTED"),
        ("APPROVED", "EXECUTING"),
        ("EXECUTING", "FILLED"),
        ("EXECUTING", "FAILED"),
        ("APPROVED", "FAILED"),  # market-hours rejection path
    }
    assert expected.issubset(STATE_TRANSITIONS)


@pytest.mark.asyncio
async def test_approve_proposal_transitions_and_emits_event(
    temp_sqlcipher_db: Any,
) -> None:
    """approve_proposal() does PENDING -> APPROVED AND appends an 'approval' event."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf)
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )
    async with sf() as session, session.begin():
        await approve_proposal(session, proposal_id, actor="U_SLACK_USER")

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "APPROVED"

        events = (
            await session.execute(
                select(Event).where(Event.event_type == "approval")
            )
        ).scalars().all()
        assert len(events) == 1
        # payload_json contains the actor + slack_action_id
        assert "U_SLACK_USER" in events[0].payload_json
        assert "approve_proposal" in events[0].payload_json


@pytest.mark.asyncio
async def test_reject_proposal_transitions_and_emits_event(
    temp_sqlcipher_db: Any,
) -> None:
    """reject_proposal() does PENDING -> REJECTED AND appends a 'rejection' event."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf)
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )
    async with sf() as session, session.begin():
        await reject_proposal(session, proposal_id, actor="U_SLACK_USER")

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "REJECTED"

        events = (
            await session.execute(
                select(Event).where(Event.event_type == "rejection")
            )
        ).scalars().all()
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Behaviors — slash command (commands.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_command_run_invokes_trigger_strategy_run(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko run ai-infra-bull` calls trigger_strategy_run via create_task."""
    from gekko.slack import commands

    captured: list[dict[str, Any]] = []

    async def fake_trigger(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"run_id": "test", "decision_id": "test", "outcome": "trade"}

    monkeypatch.setattr(commands, "trigger_strategy_run", fake_trigger)

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "run ai-infra-bull", "user_id": "U_TEST_USER"}

    await commands.handle_gekko_command(ack=ack, command=command, respond=respond)

    # ack() must have been awaited
    ack.assert_awaited()
    # And respond() told the user the trigger fired
    respond.assert_awaited()

    # Give the create_task time to run
    await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert captured[0]["strategy_name"] == "ai-infra-bull"
    # Plan 01-09 user_id fix: the slash command now passes settings.gekko_user_id
    # (internal identity for DB lookups), not the Slack id (used only as the
    # DM recipient). The clean_settings_env fixture sets gekko_user_id="test-user".
    assert captured[0]["user_id"] == "test-user"
    assert captured[0]["source"] == "slack"


@pytest.mark.asyncio
async def test_slash_command_run_with_no_name_responds_usage(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko run` with no name responds with usage and does NOT trigger."""
    from gekko.slack import commands

    captured: list[dict[str, Any]] = []

    async def fake_trigger(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(commands, "trigger_strategy_run", fake_trigger)

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "run", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(ack=ack, command=command, respond=respond)

    ack.assert_awaited()
    respond.assert_awaited()
    response_text = respond.call_args[0][0] if respond.call_args[0] else respond.call_args.kwargs.get("text", "")
    # response_text might be in args[0] or kwargs
    full = str(respond.await_args)
    assert "Usage" in full or "usage" in full
    # Trigger never fired
    await asyncio.sleep(0.05)
    assert captured == []


@pytest.mark.asyncio
async def test_slash_command_with_no_subcommand_responds_help(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko` with empty text responds with help (no trigger)."""
    from gekko.slack import commands

    captured: list[dict[str, Any]] = []

    async def fake_trigger(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(commands, "trigger_strategy_run", fake_trigger)

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(ack=ack, command=command, respond=respond)

    ack.assert_awaited()
    respond.assert_awaited()
    await asyncio.sleep(0.05)
    assert captured == []


@pytest.mark.asyncio
async def test_slash_command_acks_before_starting_background_work(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """ack() is awaited before trigger_strategy_run is scheduled (Pitfall 3)."""
    from gekko.slack import commands

    events: list[str] = []

    ack = AsyncMock(side_effect=lambda: events.append("ack"))
    respond = AsyncMock(side_effect=lambda *_a, **_k: events.append("respond"))

    async def fake_trigger(**_kwargs: Any) -> dict[str, Any]:
        events.append("trigger")
        return {}

    monkeypatch.setattr(commands, "trigger_strategy_run", fake_trigger)

    command = {"text": "run ai-infra-bull", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(ack=ack, command=command, respond=respond)
    # ack first
    assert events[0] == "ack"
    # Wait for background task
    await asyncio.sleep(0.05)
    # And the trigger fired AFTER ack (it's in events[2] or later)
    assert "trigger" in events
    assert events.index("trigger") > events.index("ack")


# ---------------------------------------------------------------------------
# Behaviors — action handlers (slack_handler.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_acks_first_and_invokes_executor(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """Approve action: ack() first; executor is fire-and-forgotten via create_task."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf, user_id="test-user")
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )

    # Patch the session factory accessor + execute_proposal
    from gekko.approval import slack_handler

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _user_id: (sf, None))

    executor_called: list[tuple[str, str]] = []

    async def fake_execute_proposal(pid: str, uid: str) -> None:
        executor_called.append((pid, uid))

    monkeypatch.setattr(slack_handler, "execute_proposal", fake_execute_proposal)

    events: list[str] = []
    ack = AsyncMock(side_effect=lambda: events.append("ack"))
    client = MagicMock()
    client.chat_postMessage = AsyncMock(
        side_effect=lambda **_k: events.append("dm")
    )

    body = {
        "actions": [{"value": proposal_id}],
        # Plan 01-09 user_id fix: body.user.id is the Slack id; must equal
        # settings.slack_user_id (set by clean_settings_env to "U_TEST_USER").
        "user": {"id": "U_TEST_USER"},
    }
    await slack_handler.handle_approve(ack=ack, body=body, client=client)
    assert events[0] == "ack"
    # Give background tasks time
    await asyncio.sleep(0.1)
    assert len(executor_called) == 1
    assert executor_called[0][0] == proposal_id


@pytest.mark.asyncio
async def test_handle_reject_does_not_invoke_executor(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """Reject action: ack first; rejection event written; executor NOT invoked."""
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf, user_id="test-user")
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="test-user", strategy_id=strategy_id
    )

    from gekko.approval import slack_handler

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _user_id: (sf, None))

    executor_called: list[Any] = []

    async def fake_execute_proposal(*args: Any, **kwargs: Any) -> None:
        executor_called.append((args, kwargs))

    monkeypatch.setattr(slack_handler, "execute_proposal", fake_execute_proposal)

    ack = AsyncMock()
    client = MagicMock()
    client.chat_postMessage = AsyncMock()
    body = {
        "actions": [{"value": proposal_id}],
        # Plan 01-09 user_id fix: body.user.id is the Slack id; must equal
        # settings.slack_user_id (set by clean_settings_env to "U_TEST_USER").
        "user": {"id": "U_TEST_USER"},
    }
    await slack_handler.handle_reject(ack=ack, body=body, client=client)
    ack.assert_awaited()
    await asyncio.sleep(0.05)
    assert executor_called == []

    # And the rejection event landed
    async with sf() as session:
        events = (
            await session.execute(
                select(Event).where(Event.event_type == "rejection")
            )
        ).scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_handle_edit_size_stub_acks_and_dms_deferred_message(
    clean_settings_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edit-size stub: ack first; sends 'coming in Phase 3' DM; logs deferred.

    WR-06 fix: DM now flows through the ``_send_slack_dm`` identity-split
    seam (PATTERNS §10) instead of a direct ``client.chat_postMessage``
    call. The test captures the seam invocation.
    """
    from gekko.approval import slack_handler

    events: list[str] = []
    dms: list[tuple[str, str]] = []

    async def _capture_dm(uid: str, text: str) -> None:
        dms.append((uid, text))
        events.append("dm")

    monkeypatch.setattr(
        "gekko.execution.executor._send_slack_dm", _capture_dm
    )

    ack = AsyncMock(side_effect=lambda: events.append("ack"))
    client = MagicMock()
    client.chat_postMessage = AsyncMock()  # must NOT be called
    body = {"actions": [{"value": "decision-xyz"}], "user": {"id": "U_TEST"}}
    await slack_handler.handle_edit_size_stub(
        ack=ack, body=body, client=client
    )
    assert events[0] == "ack"
    assert "dm" in events
    # The DM mentioned Phase 3 and routed through the seam (not the bolt client).
    assert client.chat_postMessage.await_count == 0
    assert len(dms) == 1
    _uid, text = dms[0]
    assert "Phase 3" in text or "phase 3" in text.lower()


@pytest.mark.asyncio
async def test_handle_escalate_stub_acks_and_dms_deferred_message(
    clean_settings_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalate stub: ack first; sends 'coming in Phase 3' DM; logs deferred.

    WR-06 fix: DM flows through the identity-split seam.
    """
    from gekko.approval import slack_handler

    events: list[str] = []
    dms: list[tuple[str, str]] = []

    async def _capture_dm(uid: str, text: str) -> None:
        dms.append((uid, text))
        events.append("dm")

    monkeypatch.setattr(
        "gekko.execution.executor._send_slack_dm", _capture_dm
    )

    ack = AsyncMock(side_effect=lambda: events.append("ack"))
    client = MagicMock()
    client.chat_postMessage = AsyncMock()  # must NOT be called
    body = {"actions": [{"value": "decision-xyz"}], "user": {"id": "U_TEST"}}
    await slack_handler.handle_escalate_stub(
        ack=ack, body=body, client=client
    )
    assert events[0] == "ack"
    assert "dm" in events
    assert client.chat_postMessage.await_count == 0
    assert len(dms) == 1
    _uid, text = dms[0]
    assert "Phase 3" in text or "phase 3" in text.lower()


@pytest.mark.asyncio
async def test_handle_approve_refuses_cross_user_action(
    temp_sqlcipher_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """V4 Access Control — if body.user.id != proposal.user_id, refuse + DM.

    WR-06 fix: the refusal DM now routes through the ``_send_slack_dm``
    identity-split seam (PATTERNS §10) instead of a direct
    ``client.chat_postMessage`` call.
    """
    sf = make_session_factory(temp_sqlcipher_db)
    strategy_id = await _seed_user_and_strategy(sf, user_id="real-owner")
    proposal_id = uuid4().hex
    await _seed_pending_proposal(
        sf, proposal_id=proposal_id, user_id="real-owner", strategy_id=strategy_id
    )

    from gekko.approval import slack_handler

    monkeypatch.setattr(slack_handler, "_get_session_factory", lambda _user_id: (sf, None))

    executor_called: list[Any] = []

    async def fake_execute_proposal(*args: Any, **kwargs: Any) -> None:
        executor_called.append((args, kwargs))

    monkeypatch.setattr(slack_handler, "execute_proposal", fake_execute_proposal)

    dms: list[tuple[str, str]] = []

    async def _capture_dm(uid: str, text: str) -> None:
        dms.append((uid, text))

    monkeypatch.setattr(
        "gekko.execution.executor._send_slack_dm", _capture_dm
    )

    ack = AsyncMock()
    client = MagicMock()
    client.chat_postMessage = AsyncMock()  # must NOT be called

    # Foreign Slack user clicks Approve on someone else's proposal
    body = {
        "actions": [{"value": proposal_id}],
        "user": {"id": "evil-user"},
    }
    await slack_handler.handle_approve(ack=ack, body=body, client=client)
    ack.assert_awaited()
    await asyncio.sleep(0.05)

    # Executor NOT called
    assert executor_called == []
    # DM was sent through the seam (not the bolt client) and mentions
    # "not the owner".
    assert client.chat_postMessage.await_count == 0
    assert len(dms) == 1
    _uid, text = dms[0]
    assert "not the owner" in text.lower() or "not authorized" in text.lower()
