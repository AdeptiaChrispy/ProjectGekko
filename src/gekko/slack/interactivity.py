"""Bolt action + command registration — Plan 01-08 Task 3.

Registers the four ``@app.action(...)`` handlers and the ``/gekko``
slash command against the :data:`gekko.slack.app.slack_app` singleton.
Importing this module is the side-effect that wires the handlers — Plan
01-09's FastAPI lifespan imports it during startup.

Keeping registration in a dedicated module (rather than in
:mod:`gekko.slack.commands` / :mod:`gekko.approval.slack_handler`) lets
the testable logic stay decoupled from the singleton. Tests
:func:`monkeypatch.setattr` the underlying functions and never touch
the registration layer.
"""

from __future__ import annotations

from typing import Any

from gekko.approval.slack_handler import (
    handle_approve,
    handle_edit_size,
    handle_edit_size_view_submission,
    handle_escalate_stub,
    handle_reject,
)
from gekko.slack.app import slack_app
from gekko.slack.commands import handle_gekko_command


@slack_app.command("/gekko")
async def _gekko_command(ack: Any, command: dict[str, Any], respond: Any) -> None:
    """Bolt-side wrapper — delegates to :func:`handle_gekko_command`."""
    await handle_gekko_command(ack=ack, command=command, respond=respond)


@slack_app.action("approve_proposal")
async def _approve(ack: Any, body: dict[str, Any], client: Any) -> None:
    """Bolt-side wrapper — delegates to :func:`handle_approve`."""
    await handle_approve(ack=ack, body=body, client=client)


@slack_app.action("reject_proposal")
async def _reject(ack: Any, body: dict[str, Any], client: Any) -> None:
    """Bolt-side wrapper — delegates to :func:`handle_reject`."""
    await handle_reject(ack=ack, body=body, client=client)


@slack_app.action("edit_size")
async def _edit_size(ack: Any, body: dict[str, Any], client: Any) -> None:
    """Bolt-side wrapper — opens Block Kit edit-size modal (D-54, Plan 03-05)."""
    await handle_edit_size(ack=ack, body=body, client=client)


@slack_app.view("edit_size_modal")
async def _edit_size_submit(
    ack: Any, body: dict[str, Any], client: Any, view: dict[str, Any]
) -> None:
    """view_submission handler — drift check + state transition (D-54, Plan 03-05)."""
    await handle_edit_size_view_submission(ack=ack, body=body, client=client, view=view)


@slack_app.action("escalate_to_dashboard")
async def _escalate(ack: Any, body: dict[str, Any], client: Any) -> None:
    """Bolt-side wrapper — deprecated (D-60). URL button replaces action button."""
    await handle_escalate_stub(ack=ack, body=body, client=client)


__all__: tuple[str, ...] = ()
