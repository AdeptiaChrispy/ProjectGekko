"""Slack action handlers — Plan 01-08 Task 3 (HITL-04).

Four ``@app.action(...)`` handlers register here (registration happens in
:mod:`gekko.slack.interactivity`; this module owns the logic):

* :func:`handle_approve` — PENDING -> APPROVED + fire Executor.
* :func:`handle_reject` — PENDING -> REJECTED. No Executor.
* :func:`handle_edit_size_stub` — P3-deferred. DMs "coming in Phase 3".
* :func:`handle_escalate_stub` — P3-deferred. DMs "coming in Phase 3".

The Pitfall 3 invariant per RESEARCH §"Slack Bolt + FastAPI adapter
wiring": ``ack()`` is the FIRST awaited call in every handler. Slack
times out interactivity payloads after 3 seconds — any DB/broker work
done before ack risks Slack retrying (T-01-08-03 in the threat model).

Cross-user defense (V4 Access Control / T-01-08-01): when
``body['user']['id'] != proposal.user_id`` the handler refuses and
DMs "not the owner". The check happens AFTER ack but BEFORE any state
mutation.

Test seams:
  * :data:`execute_proposal` — module-level reference so tests can
    monkeypatch the executor without touching the import path.
  * :func:`_get_session_factory` — module-level so tests pass a pre-built
    factory (and ``None`` for the engine, which means "don't dispose").

Production wiring (Plan 01-09):
  * The CLI bootstrap calls
    :func:`gekko.agent.runtime.set_passphrase` with the operator-supplied
    passphrase BEFORE any Slack request can fire (D-19).
  * :func:`_get_session_factory` reads that cached passphrase and builds a
    per-user SQLCipher engine each call. The engine is disposed in a
    ``finally`` block so we don't leak SQLCipher connections.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.approval.proposals import (
    approve_proposal,
    reject_proposal,
    transition_status,
)
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import StrategyMetadata
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.execution.executor import execute_proposal
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: Bolt-supplied ack/client callable shapes — duck-typed.
_AckFn = Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# Session factory accessor — test seam
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Production: opens a per-user SQLCipher engine via the cached passphrase
    (Plan 01-09's CLI bootstrap populates the cache). Returns the engine
    so the caller can dispose it.

    Tests: :func:`monkeypatch.setattr` replaces this with a lambda that
    returns ``(pre_built_factory, None)``. ``None`` signals "do not
    dispose" — the test owns the engine's lifecycle via the
    ``temp_sqlcipher_db`` fixture.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def handle_approve(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Approve button — HITL-04.

    Flow:
      1. ack FIRST (Pitfall 3).
      2. Dispatch background workflow via :func:`asyncio.create_task` so
         the bolt handler returns quickly.

    The background workflow does the owner check, transitions
    PENDING -> APPROVED with the ``approval`` audit event, and fires the
    Executor.
    """
    await ack()
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]
    asyncio.create_task(
        _approve_workflow(
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            client=client,
        )
    )


async def _approve_workflow(
    *, decision_id: str, slack_user_id: str, client: Any
) -> None:
    """Background half of the approve handler.

    ``slack_user_id`` is the Slack id of the clicker (used for the
    cross-user check + DM channel). ``gekko_user_id`` is the INTERNAL
    identity (from settings.gekko_user_id) used for DB / engine /
    executor calls. The two are usually different even for the same
    human — REG-03 + REG-04.
    """
    from gekko.config import get_settings

    settings = get_settings()
    gekko_user_id = settings.gekko_user_id

    # V4 access control — refuse any Slack id other than the configured
    # operator. Per-process per-user-isolated runtime means there's
    # exactly one valid clicker.
    if slack_user_id != settings.slack_user_id:
        log.warning(
            "slack.approval.cross_user_refused",
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            configured_user_id=settings.slack_user_id,
        )
        await client.chat_postMessage(
            channel=slack_user_id,
            text="You are not the owner of this proposal.",
        )
        return

    sf, engine = _get_session_factory(gekko_user_id)
    try:
        # Snapshot account_mode + strategy_name from the LOCKED proposal row,
        # plus the first_live_trade stamp from StrategyMetadata. These
        # decide whether to take the standard approve path or divert to
        # the HITL-06 dual-channel branch. Reading account_mode from the
        # proposal row (NOT from strategy state) closes the TOCTOU
        # window per BLOCKER #5.
        proposal_account_mode: str | None = None
        strategy_name_snapshot: str | None = None
        is_live_first: bool = False
        async with sf() as session, session.begin():
            row = await session.get(ProposalRow, decision_id)
            if row is None:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=f"Proposal `{decision_id}` not found.",
                )
                return
            proposal_account_mode = row.account_mode
            # Pull strategy_name from the persisted TradeProposal payload
            # so we can look up StrategyMetadata.
            from gekko.schemas.proposal import TradeProposal as _TP

            try:
                _tp = _TP.model_validate_json(row.payload_json)
                strategy_name_snapshot = _tp.strategy_name
            except Exception:  # noqa: BLE001 — defensive
                strategy_name_snapshot = None

            # Compute is_live_first ONLY from the proposal row + metadata
            # stamp. Re-reading strategy.mode or live_mode_eligible here
            # would reopen the TOCTOU window — those gates already fired
            # at proposal-build time (T0).
            if proposal_account_mode == "LIVE" and strategy_name_snapshot:
                meta = await session.get(
                    StrategyMetadata,
                    (gekko_user_id, strategy_name_snapshot),
                )
                is_live_first = (
                    meta is None
                    or meta.first_live_trade_confirmed_at is None
                )

            if is_live_first:
                # HITL-06 dual-channel: divert PENDING → AWAITING_2ND_CHANNEL.
                # Do NOT dispatch the executor here; the dashboard
                # /live-confirm route fires it once the second channel
                # confirms.
                await transition_status(
                    session,
                    decision_id,
                    from_status="PENDING",
                    to_status="AWAITING_2ND_CHANNEL",
                )
                await append_event(
                    session,
                    user_id=row.user_id,
                    strategy_id=row.strategy_id,
                    event_type="approval",
                    payload={
                        "proposal_id": decision_id,
                        "actor": slack_user_id,
                        "slack_action_id": "approve_proposal",
                        "awaiting_2nd_channel": True,
                    },
                )
            else:
                # Standard single-channel approve (Phase-1 path).
                await approve_proposal(
                    session, decision_id, actor=slack_user_id
                )

        if is_live_first:
            # DM the operator the dashboard URL — AFTER the transaction
            # commits so the row state on disk matches what the dashboard
            # route will see. Read dashboard_url from settings (fallback
            # to a sentinel string when unset so the message still
            # rendsers).
            dashboard_url = getattr(
                settings, "dashboard_url", "http://localhost:8000"
            )
            await client.chat_postMessage(
                channel=slack_user_id,
                text=(
                    f"⚠️ This is your FIRST live trade for "
                    f"`{strategy_name_snapshot}`. To execute, also "
                    f"click confirm in your dashboard at "
                    f"{dashboard_url}/live-confirm/{decision_id}"
                ),
            )
            return

        # Outside the approval transaction — the Executor opens its own.
        # Note: pass gekko_user_id (internal id), NOT slack_user_id, so
        # the executor opens the per-user DB at the right path.
        asyncio.create_task(
            execute_proposal(decision_id, gekko_user_id)
        )
        await client.chat_postMessage(
            channel=slack_user_id,
            text=f"Approved `{decision_id}`. Placing order…",
        )
    except Exception:
        log.exception(
            "slack.approval.workflow_failed",
            decision_id=decision_id,
            gekko_user_id=gekko_user_id,
            slack_user_id=slack_user_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


async def handle_reject(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Reject button — HITL-04. ack-first, then background reject workflow."""
    await ack()
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]
    asyncio.create_task(
        _reject_workflow(
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            client=client,
        )
    )


async def _reject_workflow(
    *, decision_id: str, slack_user_id: str, client: Any
) -> None:
    """Background half of the reject handler. Same identity model as approve."""
    from gekko.config import get_settings

    settings = get_settings()
    gekko_user_id = settings.gekko_user_id

    if slack_user_id != settings.slack_user_id:
        log.warning(
            "slack.rejection.cross_user_refused",
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            configured_user_id=settings.slack_user_id,
        )
        await client.chat_postMessage(
            channel=slack_user_id,
            text="You are not the owner of this proposal.",
        )
        return

    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session, session.begin():
            row = await session.get(ProposalRow, decision_id)
            if row is None:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=f"Proposal `{decision_id}` not found.",
                )
                return
            await reject_proposal(
                session, decision_id, actor=slack_user_id
            )
        await client.chat_postMessage(
            channel=slack_user_id,
            text=f"Rejected `{decision_id}`. No order will be placed.",
        )
    except Exception:
        log.exception(
            "slack.rejection.workflow_failed",
            decision_id=decision_id,
            gekko_user_id=gekko_user_id,
            slack_user_id=slack_user_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# P3-deferred stubs
# ---------------------------------------------------------------------------


async def handle_edit_size_stub(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Edit-size button — deferred to Plan 03 (HITL UX hardening)."""
    await ack()
    slack_user_id = body["user"]["id"]
    await client.chat_postMessage(
        channel=slack_user_id,
        text=(
            "Edit-size is coming in Phase 3. Click Approve or Reject for now."
        ),
    )
    log.warning("feature.deferred", feature="edit_size", phase="P3")


async def handle_escalate_stub(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Escalate-to-dashboard button — deferred to Plan 03."""
    await ack()
    slack_user_id = body["user"]["id"]
    await client.chat_postMessage(
        channel=slack_user_id,
        text="Escalation to the dashboard is coming in Phase 3.",
    )
    log.warning(
        "feature.deferred", feature="escalate_to_dashboard", phase="P3"
    )


__all__: tuple[str, ...] = (
    "handle_approve",
    "handle_edit_size_stub",
    "handle_escalate_stub",
    "handle_reject",
)
