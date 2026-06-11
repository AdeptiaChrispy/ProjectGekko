"""Slack Bolt AsyncApp + FastAPI adapter — Plan 01-08 Task 3.

Per RESEARCH §"Code Examples — Slack Bolt + FastAPI adapter wiring":
``slack-bolt`` ships an ``AsyncSlackRequestHandler`` that bridges its
Async ``App`` into FastAPI by exposing a ``handle(request)`` method we
mount on a single ``POST /slack/events`` route (Plan 01-09 wires the
route into the FastAPI app).

Signing-secret verification is automatic — slack-bolt validates every
inbound request against ``slack_signing_secret`` so we never roll our
own HMAC (RESEARCH §"Don't Hand-Roll").

Module-level instantiation is intentional: a single Bolt app + handler
pair is reused across slash commands and action handlers. The
import-time singleton requires :class:`gekko.config.Settings` to have
``SLACK_BOT_TOKEN`` and ``SLACK_SIGNING_SECRET`` available; in tests
that don't exercise this module they aren't imported.

Plan 01-09 owns the FastAPI lifespan that:

  * imports :mod:`gekko.slack.interactivity` (which registers the four
    ``@slack_app.action(...)`` handlers) and
    :mod:`gekko.slack.commands` (for the ``/gekko`` slash command), and
  * mounts the handler at ``POST /slack/events``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

from gekko.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    pass


_settings = get_settings()

#: Process-wide Bolt async app. Slash commands and action handlers
#: register against this singleton.
slack_app: AsyncApp = AsyncApp(
    token=_settings.slack_bot_token.get_secret_value(),
    signing_secret=_settings.slack_signing_secret.get_secret_value(),
)

#: FastAPI bridge for the Bolt app. Plan 01-09 mounts ``handler.handle``
#: on ``POST /slack/events``.
slack_handler: AsyncSlackRequestHandler = AsyncSlackRequestHandler(slack_app)


__all__: tuple[str, ...] = ("slack_app", "slack_handler")
