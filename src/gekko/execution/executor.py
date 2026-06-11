"""Deterministic order executor — Plan 01-08 Task 4.

Per RESEARCH §"Anti-Patterns" Pattern 1, the LLM **never** calls the
broker directly. The Executor is the deterministic Python boundary:

  1. Load the persisted ``Proposal`` row + validated ``TradeProposal``
     payload.
  2. Run the market-hours guard (EXEC-10 / Plan 01-08 Task 2).
  3. Construct an ``OrderRequest`` using the persisted, deterministic
     ``client_order_id`` (D-20).
  4. Call :meth:`AlpacaBroker.place_order` (Plan 01-05).
  5. Append the ``order_submitted`` audit event and transition the row
     APPROVED -> EXECUTING.

The ``fill`` event + EXECUTING -> FILLED transition land in
:func:`on_fill_event`, called from :class:`AlpacaFillStream`'s websocket
callback (Plan 01-08 Task 4 wiring; Plan 01-09 owns the lifespan
startup that registers the stream).

This module is the "fired" half of the Slack approval handler — the
handler calls ``asyncio.create_task(execute_proposal(...))`` and walks
away. Errors are surfaced through audit events and Slack DMs, never
raised back to the caller (there's no caller to raise to).

Task 3 currently uses a minimal stub: Task 4 will fill in the broker /
fill-stream behavior and add the integration test. The stub exists so
:mod:`gekko.approval.slack_handler` (Task 3) can import
:func:`execute_proposal` at module level — its tests
:func:`monkeypatch.setattr` the symbol regardless of body.
"""

from __future__ import annotations

from gekko.logging_config import get_logger

log = get_logger(__name__)


async def execute_proposal(proposal_id: str, user_id: str) -> None:
    """Deterministic execution of an approved proposal.

    Task 3 stub: this is a placeholder so :mod:`gekko.approval.slack_handler`
    can import the symbol. Task 4 expands the full flow (market-hours
    guard, broker call, audit events, state transitions).

    NB: Plan 01-08 success criterion 7 — this module contains NO imports
    from ``claude_agent_sdk``. The Executor is the deterministic Python
    firewall between the LLM and the broker.
    """
    log.info(
        "executor.invoked_stub",
        proposal_id=proposal_id,
        user_id=user_id,
        note="Task 4 will expand to real broker call",
    )


__all__: tuple[str, ...] = ("execute_proposal",)
