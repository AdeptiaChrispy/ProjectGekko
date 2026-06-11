"""``/gekko run <strategy>`` slash command handler — Plan 01-08 Task 3.

The slash command is one of D-06's three trigger surfaces (the other two
are the dashboard "Run now" button in Plan 01-09 and the APScheduler
cadence). Per STRAT-05 strategies are selected by name; the user types
``/gekko run ai-infra-bull`` and the handler dispatches to
:func:`gekko.agent.runtime.trigger_strategy_run`.

Two invariants per RESEARCH Pitfall 3:

1. ``ack()`` is the FIRST awaited call. Slack times out the slash
   command after 3 seconds — any work done before ``ack()`` eats into
   that budget.
2. ``trigger_strategy_run`` is fire-and-forgotten via
   :func:`asyncio.create_task`. The agent run takes tens of seconds
   (Researcher tools + two ``query()`` calls); we must not block ack.

Test isolation: this module imports ``trigger_strategy_run`` at module
level so tests can :func:`monkeypatch.setattr` it (the runtime function
itself is exercised by :mod:`tests.unit.test_agent_runtime`).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from gekko.agent.runtime import trigger_strategy_run
from gekko.logging_config import get_logger

log = get_logger(__name__)

#: Help text shown when the user types ``/gekko`` with no subcommand.
_HELP_TEXT: str = (
    "*Gekko commands*\n"
    "• `/gekko run <strategy-name>` — kick off a one-off agent run for "
    "the named strategy.\n"
    "Strategies are managed in the dashboard or via the chat compiler."
)

#: Usage text shown when the user types ``/gekko run`` with no strategy.
_USAGE_RUN: str = (
    "Usage: `/gekko run <strategy-name>`\n"
    "Example: `/gekko run ai-infra-bull`"
)

#: Slack ``ack``-style callable accepting either ``()`` or ``(text=...)``.
_AckFn = Callable[..., Awaitable[None]]

#: Slack ``respond``-style callable. The slack-bolt ``respond`` accepts
#: either a plain text string positional arg or ``text=``/``blocks=`` kwargs.
_RespondFn = Callable[..., Awaitable[Any]]


async def handle_gekko_command(
    *,
    ack: _AckFn,
    command: dict[str, Any],
    respond: _RespondFn,
) -> None:
    """Slash command entry — D-06 / STRAT-05.

    Slack delivers ``command`` as a dict with at least ``text`` and
    ``user_id`` keys. We split ``text`` on whitespace:

    * ``""`` -> show help, do NOT trigger anything.
    * ``"run"`` -> show usage, do NOT trigger.
    * ``"run <name>"`` -> fire ``trigger_strategy_run`` in the background.

    :param ack: The bolt-supplied ack callable. Awaited FIRST.
    :param command: The Slack command dict. We read ``text`` and ``user_id``.
    :param respond: The bolt-supplied respond callable. Used for user-visible
        feedback ("Triggered ai-infra-bull..."); the agent run itself posts
        the proposal card via the reporter.
    """
    # Pitfall 3: ack FIRST. Anything that awaits before this risks the
    # 3-second Slack deadline.
    await ack()

    text = (command.get("text") or "").strip()
    user_id = command.get("user_id") or ""

    if not text:
        await respond(_HELP_TEXT)
        return

    parts = text.split()
    subcommand = parts[0].lower()

    if subcommand != "run":
        await respond(_HELP_TEXT)
        return

    if len(parts) < 2:
        await respond(_USAGE_RUN)
        return

    strategy_name = parts[1]

    # Immediate user feedback — the run itself can take 30+ seconds.
    await respond(
        f"Triggered `{strategy_name}` — I'll DM the proposal when the "
        "agent finishes."
    )

    # Fire-and-forget the actual orchestrator. The runtime emits its own
    # audit events; we don't await the result here.
    asyncio.create_task(
        trigger_strategy_run(
            user_id=user_id,
            strategy_name=strategy_name,
            source="slack",
        )
    )

    log.info(
        "slack.slash_command.triggered",
        user_id=user_id,
        strategy_name=strategy_name,
        source="slack",
    )


__all__: tuple[str, ...] = ("handle_gekko_command", "trigger_strategy_run")
