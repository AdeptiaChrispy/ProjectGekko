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

from gekko.approval.proposals import approve_proposal, reject_proposal
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Proposal as ProposalRow
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

    Separated from :func:`handle_approve` so the bolt handler returns
    after ``ack()`` while the DB/Executor work proceeds in the
    background. Errors are logged but do not propagate (we're inside
    ``asyncio.create_task`` — there's no caller to raise to).
    """
    sf, engine = _get_session_factory(slack_user_id)
    try:
        async with sf() as session, session.begin():
            row = await session.get(ProposalRow, decision_id)
            if row is None:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=f"Proposal `{decision_id}` not found.",
                )
                return
            # V4 access control — T-01-08-01.
            if row.user_id != slack_user_id:
                log.warning(
                    "slack.approval.cross_user_refused",
                    decision_id=decision_id,
                    proposal_owner=row.user_id,
                    slack_user_id=slack_user_id,
                )
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text="You are not the owner of this proposal.",
                )
                return
            await approve_proposal(
                session, decision_id, actor=slack_user_id
            )
        # Outside the approval transaction — the Executor opens its own.
        asyncio.create_task(
            execute_proposal(decision_id, slack_user_id)
        )
        await client.chat_postMessage(
            channel=slack_user_id,
            text=f"Approved `{decision_id}`. Placing order…",
        )
    except Exception:
        log.exception(
            "slack.approval.workflow_failed",
            decision_id=decision_id,
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
    """Background half of the reject handler."""
    sf, engine = _get_session_factory(slack_user_id)
    try:
        async with sf() as session, session.begin():
            row = await session.get(ProposalRow, decision_id)
            if row is None:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=f"Proposal `{decision_id}` not found.",
                )
                return
            if row.user_id != slack_user_id:
                log.warning(
                    "slack.rejection.cross_user_refused",
                    decision_id=decision_id,
                    proposal_owner=row.user_id,
                    slack_user_id=slack_user_id,
                )
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text="You are not the owner of this proposal.",
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
