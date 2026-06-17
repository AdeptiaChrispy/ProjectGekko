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
    "• `/gekko kill CONFIRM` — halt all trading immediately (two-step).\n"
    "• `/gekko unkill UNKILL` — resume trading after a kill.\n"
    "Strategies are managed in the dashboard or via the chat compiler."
)

#: Two-step warning shown when the user types ``/gekko kill`` without ``CONFIRM``.
_KILL_WARN_TEXT: str = (
    "⚠️ Type `/gekko kill CONFIRM` within 60 seconds to halt all trading. "
    "This cancels all open orders across every strategy. "
    "Kill state persists across process restarts."
)

#: Shown when ``/gekko kill <arg>`` is anything other than ``CONFIRM``.
_KILL_MISMATCH_TEXT: str = (
    "Type `/gekko kill CONFIRM` exactly to halt trading. "
    "Other input is ignored."
)

#: Two-step warning shown when the user types ``/gekko unkill`` without ``UNKILL``.
#: WR-01 fix: literal token aligned with the spec invariant #6
#: (``/gekko kill CONFIRM`` / ``/gekko unkill UNKILL``) and with the CLI
#: (``cli.py`` requires ``UNKILL``). Previously CONFIRM was used for
#: both, causing copy drift between surfaces and breaking operator
#: muscle memory under stress.
_UNKILL_WARN_TEXT: str = (
    "⚠️ Type `/gekko unkill UNKILL` to resume trading. "
    "Note: previously-cancelled orders are NOT restored."
)

#: Shown when ``/gekko unkill <arg>`` is anything other than ``UNKILL``.
_UNKILL_MISMATCH_TEXT: str = (
    "Type `/gekko unkill UNKILL` exactly to resume trading. "
    "Other input is ignored."
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
    slack_user_id = command.get("user_id") or ""

    # Cross-user defense (V4): only the configured operator (the one
    # whose Slack id is in SLACK_USER_ID) can trigger runs. Slack
    # delivers the slash-command sender's id in `command["user_id"]`;
    # we compare it to `settings.slack_user_id`. Per-process per-user-
    # isolated runtime (REG-03) means there's exactly one valid operator.
    from gekko.config import get_settings

    settings = get_settings()
    if slack_user_id and slack_user_id != settings.slack_user_id:
        log.warning(
            "slack.slash_command.cross_user_refused",
            slack_user_id=slack_user_id,
            configured_user_id=settings.slack_user_id,
        )
        await respond(
            "This Gekko instance is bound to a different operator's "
            "Slack id. Sender refused."
        )
        return

    if not text:
        await respond(_HELP_TEXT)
        return

    parts = text.split()
    subcommand = parts[0].lower()

    if subcommand == "kill":
        await _handle_kill_command(respond=respond, args=parts[1:])
        return

    if subcommand == "unkill":
        await _handle_unkill_command(respond=respond, args=parts[1:])
        return

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

    # Fire-and-forget the orchestrator + the proposal-card post.
    #
    # `gekko_user_id` is the INTERNAL identity (DB / audit-log scoping).
    # `slack_user_id` is the EXTERNAL identity (Slack channel + DM
    # recipient). For the Slack trigger surface they're often two
    # different strings even though they refer to the same person —
    # the strategy was saved with `gekko_user_id`, so the agent must
    # look it up by that key.
    asyncio.create_task(
        _run_and_post(
            gekko_user_id=settings.gekko_user_id,
            slack_user_id=slack_user_id or settings.slack_user_id,
            strategy_name=strategy_name,
        )
    )

    log.info(
        "slack.slash_command.triggered",
        gekko_user_id=settings.gekko_user_id,
        slack_user_id=slack_user_id,
        strategy_name=strategy_name,
        source="slack",
    )


async def _run_and_post(
    *,
    gekko_user_id: str,
    slack_user_id: str,
    strategy_name: str,
) -> None:
    """Background wrapper: run the agent + post the resulting card.

    ``gekko_user_id`` keys DB / audit-log operations. ``slack_user_id``
    is the DM channel where the resulting card / error notification
    lands. Errors at either step are logged but never re-raised (we're
    inside ``asyncio.create_task``).
    """
    try:
        result = await trigger_strategy_run(
            user_id=gekko_user_id,
            strategy_name=strategy_name,
            source="slack",
        )
    except Exception:
        log.exception(
            "slack.run.trigger_failed",
            gekko_user_id=gekko_user_id,
            strategy_name=strategy_name,
        )
        await _post_error_dm(slack_user_id, strategy_name)
        return
    try:
        from gekko.reporter.slack import post_run_result

        await post_run_result(slack_user_id, result)
    except Exception:
        log.exception(
            "slack.run.post_failed",
            gekko_user_id=gekko_user_id,
            strategy_name=strategy_name,
        )


async def _post_error_dm(slack_user_id: str, strategy_name: str) -> None:
    """Best-effort 'run failed' DM. Swallows its own errors."""
    try:
        from gekko.slack.app import slack_app

        await slack_app.client.chat_postMessage(
            channel=slack_user_id,
            text=(
                f"Run for `{strategy_name}` failed. Check `gekko serve` "
                "logs for the traceback."
            ),
        )
    except Exception:  # noqa: BLE001
        log.exception("slack.error_dm.failed", slack_user_id=slack_user_id)


# ---------------------------------------------------------------------------
# /gekko kill + /gekko unkill — Plan 02-05 Task 2 (D-38 / EXEC-06)
# ---------------------------------------------------------------------------


async def _handle_kill_command(
    *, respond: _RespondFn, args: list[str]
) -> None:
    """Two-step Slack kill flow per UI-SPEC §2 Slack parallel.

    * ``/gekko kill`` (no arg) → warn the operator + ask for CONFIRM
    * ``/gekko kill CONFIRM`` → fire ``_execute_kill`` in the background
    * ``/gekko kill <anything-else>`` → reject with the mismatch message

    The cross-user defense + ``await ack()`` ran in the parent dispatcher
    before we got here, so this handler is reachable only for the
    configured operator.
    """
    from gekko.config import get_settings

    settings = get_settings()

    if not args:
        await respond(_KILL_WARN_TEXT)
        return

    if args[0].strip().upper() != "CONFIRM":
        await respond(_KILL_MISMATCH_TEXT)
        return

    # CONFIRM was typed — fire-and-forget the background kill. The DM
    # with the tally is sent by `_execute_kill` itself via the executor's
    # `_send_slack_dm` seam (identity-split safe).
    asyncio.create_task(
        _execute_kill_background(
            user_id=settings.gekko_user_id, source="slack"
        )
    )

    await respond(
        "🚫 Halting all trading… the bot will DM you when the cancel "
        "sweep completes (5-second SLA)."
    )

    log.info(
        "slack.kill.confirmed",
        gekko_user_id=settings.gekko_user_id,
        source="slack",
    )


async def _handle_unkill_command(
    *, respond: _RespondFn, args: list[str]
) -> None:
    """Two-step Slack unkill flow per UI-SPEC §2 Slack parallel.

    WR-01 fix: requires the literal ``UNKILL`` argument (matching the
    CLI ``gekko unkill UNKILL`` token and the spec invariant #6 — two
    distinct tokens, one per surface). Previously this required
    ``CONFIRM`` symmetric with ``/gekko kill``; the asymmetry was
    intentional in the spec so muscle memory cleanly distinguishes the
    two destructive operations.

    Per UI-SPEC the unkill DOES NOT restore previously-cancelled orders;
    the warn message includes that.
    """
    from gekko.config import get_settings

    settings = get_settings()

    if not args:
        await respond(_UNKILL_WARN_TEXT)
        return

    if args[0].strip().upper() != "UNKILL":
        await respond(_UNKILL_MISMATCH_TEXT)
        return

    asyncio.create_task(
        _execute_unkill_background(
            user_id=settings.gekko_user_id, source="slack"
        )
    )

    await respond(
        "✅ Resuming trading… the bot will DM you when unkill completes."
    )

    log.info(
        "slack.unkill.confirmed",
        gekko_user_id=settings.gekko_user_id,
        source="slack",
    )


async def _execute_kill_background(*, user_id: str, source: str) -> None:
    """Background wrapper for ``_execute_kill`` — catches errors so create_task
    doesn't drop them silently. PATTERNS §5d.
    """
    try:
        from gekko.execution.kill_switch import _execute_kill

        await _execute_kill(user_id=user_id, source=source, reason="manual")
    except Exception:  # noqa: BLE001
        log.exception(
            "slack.kill.background_failed", user_id=user_id, source=source
        )


async def _execute_unkill_background(*, user_id: str, source: str) -> None:
    """Background wrapper for ``_execute_unkill``. PATTERNS §5d."""
    try:
        from gekko.execution.kill_switch import _execute_unkill

        await _execute_unkill(user_id=user_id, source=source)
    except Exception:  # noqa: BLE001
        log.exception(
            "slack.unkill.background_failed", user_id=user_id, source=source
        )


__all__: tuple[str, ...] = ("handle_gekko_command", "trigger_strategy_run")
