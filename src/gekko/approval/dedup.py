"""Dedup gate for Slack / Dashboard action handlers — Plan 03-02 Task 1 (HITL-02).

The single entry point is :func:`claim_action` — a belt-and-suspenders
idempotency guard that inserts a :class:`~gekko.db.models.SlackActionDedup`
row under a UNIQUE constraint and returns either ``"first_write"`` or
``"duplicate"``.

Design follows the two-analog stack from PATTERNS §1a / §2b:

* **Flush + IntegrityError shape:** Mirrors
  ``src/gekko/agent/proposal_writer.py:290-329`` — ``session.add()`` the row,
  ``await session.flush()``, catch ``sqlalchemy.exc.IntegrityError`` (the
  UNIQUE constraint fires), ``await session.rollback()`` (MANDATORY — the
  IntegrityError leaves the session in an aborted-transaction state), then
  open a fresh session to record the ``dedup_click`` audit event.

* **Fresh-session idiom:** Mirrors PATTERNS §2d — a module-local
  ``_get_session_factory(actor_gekko_user_id)`` shim that tests can
  monkeypatch without touching the caller's outer transaction context.

Security invariants:
  - This module MUST NOT import ``claude_agent_sdk`` or ``anthropic``.
    The ``test_executor_module_does_not_import_claude_agent_sdk`` gate shape
    from Plan 01-08 applies here; the dedup helper is a deterministic Python
    firewall on the broker-execution path.

References:
  * CONTEXT.md D-41 + D-42 + D-44 + D-45
  * PATTERNS §1a + §2b + §2d
  * UI-SPEC §Surface 7 (duplicate ephemeral copy)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.db.models import SlackActionDedup
from gekko.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Session factory accessor — test seam (PATTERNS §2d)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[object, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Test seam: monkeypatch this with ``lambda _uid: (sf, None)`` to reuse
    the test's in-memory engine without a passphrase round-trip.
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.vault.passphrase import get_passphrase as _gp

    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), _gp())
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# claim_action
# ---------------------------------------------------------------------------


async def claim_action(
    session: AsyncSession,
    *,
    proposal_id: str,
    action_id: str,
    actor_slack_user_id: str | None,
    actor_gekko_user_id: str,
    source: str,
    slack_trigger_id: str | None = None,
) -> Literal["first_write", "duplicate"]:
    """Atomically claim a Slack / Dashboard / CLI action via UNIQUE INSERT.

    On **first call** for a given ``(proposal_id, action_id,
    actor_slack_user_id)`` (Slack UNIQUE per D-42) or ``(proposal_id,
    action_id, actor_gekko_user_id, source)`` (dashboard UNIQUE per D-56):

    * Inserts a :class:`~gekko.db.models.SlackActionDedup` row with
      ``result="first_write"``.
    * Flushes the session (raises ``IntegrityError`` on collision).
    * Returns ``"first_write"`` — the caller proceeds with the state-machine
      transition.

    On **duplicate call** (``IntegrityError`` fires):

    * Calls ``await session.rollback()`` (MANDATORY — IntegrityError aborts
      the transaction context).
    * Opens a **fresh** session via ``_get_session_factory`` to write a
      ``dedup_click`` audit event (the rollback above would silently drop any
      event appended to the original session).
    * Returns ``"duplicate"`` — the caller fires the D-43 ephemeral and
      returns without touching state.

    :param session: The caller's outer async session.  The caller is
        responsible for opening a ``session.begin()`` block BEFORE calling
        this helper.  On ``"first_write"`` the session is left open so the
        caller can append further audit events and commit.  On ``"duplicate"``
        the session has been rolled back; the caller must NOT attempt further
        writes on it.
    :param proposal_id: UUID (as hex string) of the proposal being acted on.
    :param action_id: One of ``"approve_proposal"``, ``"reject_proposal"``,
        ``"edit_size_proposal"``.
    :param actor_slack_user_id: Slack user id of the clicker.  ``None`` for
        dashboard / CLI surfaces that have no Slack identity.
    :param actor_gekko_user_id: Internal Gekko user id (settings.gekko_user_id).
    :param source: Surface — ``"slack"``, ``"dashboard"``, or ``"cli"``.
    :param slack_trigger_id: Slack trigger_id from ``body["trigger_id"]``; used
        for retry-debugging per D-45.  Excluded from ``__repr__`` per
        T-03-01-03.
    :returns: ``"first_write"`` or ``"duplicate"``.
    """
    inserted_at = datetime.now(UTC).isoformat()

    # --- Attempt INSERT ---
    row = SlackActionDedup(
        proposal_id=proposal_id,
        action_id=action_id,
        actor_slack_user_id=actor_slack_user_id,
        actor_gekko_user_id=actor_gekko_user_id,
        source=source,
        slack_trigger_id=slack_trigger_id,
        inserted_at=inserted_at,
        result="first_write",
    )
    session.add(row)

    try:
        await session.flush()
        log.debug(
            "dedup.claim_action.first_write",
            proposal_id=proposal_id,
            action_id=action_id,
            source=source,
        )
        return "first_write"

    except IntegrityError:
        # UNIQUE constraint fired — a prior write already claimed this action.
        # MANDATORY: rollback clears the aborted transaction state before we
        # can open a new session.
        await session.rollback()

        log.debug(
            "dedup.claim_action.duplicate",
            proposal_id=proposal_id,
            action_id=action_id,
            source=source,
        )

        # Write the dedup_click audit event in a FRESH session so the rollback
        # above does not silently drop it.  The fresh session opens its own
        # engine whose lifecycle is owned by the test seam (_get_session_factory).
        fresh_sf, fresh_engine = _get_session_factory(actor_gekko_user_id)
        try:
            async with fresh_sf() as fresh_session, fresh_session.begin():
                await append_event(
                    fresh_session,
                    user_id=actor_gekko_user_id,
                    strategy_id=None,
                    event_type="dedup_click",
                    payload=normalize_decimals(
                        {
                            "proposal_id": proposal_id,
                            "action_id": action_id,
                            "actor_slack_user_id": actor_slack_user_id,
                            "actor_gekko_user_id": actor_gekko_user_id,
                            "source": source,
                            "outcome": "duplicate",
                        }
                    ),
                )
        except Exception:  # noqa: BLE001 — audit failure must not mask the dedup result
            log.exception(
                "dedup.claim_action.audit_event_failed",
                proposal_id=proposal_id,
                action_id=action_id,
            )
        finally:
            if fresh_engine is not None:
                await fresh_engine.dispose()

        return "duplicate"


__all__: tuple[str, ...] = ("claim_action",)
