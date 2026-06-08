"""structlog credential-safe logging config — Plan 01-02 Task 1.

Satisfies AUTH-04 / D-25: logs **never** contain credentials, raw broker
responses, or Slack tokens. The processor chain is:

    contextvars -> add_log_level -> TimeStamper(iso/utc)
        -> stdlib add_logger_name -> _redact -> JSONRenderer

`_redact` runs BEFORE the JSON renderer so the rendered line never contains a
secret value. Both the value-pattern scan and the key-name scrub are applied.

The module does NOT call `configure_logging()` on import — callers (the CLI,
tests, the `gekko serve` startup) explicitly invoke it. This keeps tests free
to reset between scenarios.

References:
  * RESEARCH.md §"Code Examples — structlog with credential redaction"
  * CONTEXT.md D-25 (`structlog` JSON logging; logs never contain credentials)
  * VALIDATION.md row 01-02-T1
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Credential-shaped value patterns
# ---------------------------------------------------------------------------

# Bearer JWT or opaque token following the "Bearer " prefix.
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-/]+", re.IGNORECASE)

# Anthropic explicit shape — matched FIRST so we can label it distinctly.
# sk-ant-api03-... or sk-ant-admin01-... etc.
_ANTHROPIC = re.compile(r"\bsk-ant-[\w\-]+\b")

# Generic OpenAI-style key (and any other `sk-`-prefixed key).
# Run after _ANTHROPIC so `sk-ant-...` becomes <REDACTED-ANTHROPIC>.
_SK = re.compile(r"\bsk-[A-Za-z0-9._\-]{20,}\b")

# Alpaca live/paper API keys (start with `PK`, 18-20 [A-Z0-9] follow).
_ALPACA_KEY = re.compile(r"\bPK[A-Z0-9]{18,20}\b")

# Slack tokens — bot, app-level, user OAuth respectively.
_XOXB = re.compile(r"\bxoxb-[\w\-]+\b")
_XAPP = re.compile(r"\bxapp-[\w\-]+\b")
_XOXA = re.compile(r"\bxoxa-[\w\-]+\b")

# Ordering matters: the more-specific patterns run BEFORE the generic ones.
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_BEARER, "Bearer <REDACTED>"),
    (_ANTHROPIC, "<REDACTED-ANTHROPIC>"),
    (_SK, "<REDACTED-SK>"),
    (_ALPACA_KEY, "<REDACTED-ALPACA>"),
    (_XOXB, "<REDACTED-XOXB>"),
    (_XAPP, "<REDACTED-XAPP>"),
    (_XOXA, "<REDACTED-XOXA>"),
)


# ---------------------------------------------------------------------------
# Key-name redaction set (case-insensitive)
# ---------------------------------------------------------------------------

_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "secret_key",
        "passphrase",
        "password",
        "token",
        "authorization",
        "slack_token",
        "client_secret",
        # Phase 1 explicit env-var names — also blocked by name as defence-in-depth
        # in case a caller logs the raw Settings dict.
        "anthropic_api_key",
        "alpaca_paper_api_key",
        "alpaca_paper_secret_key",
        "slack_bot_token",
        "slack_signing_secret",
        "finnhub_api_key",
    }
)


# ---------------------------------------------------------------------------
# Redaction processor
# ---------------------------------------------------------------------------


def _scrub_string(value: str) -> str:
    """Apply every credential-shaped regex to a single string value."""
    for pattern, replacement in _VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _scrub_value(value: Any) -> Any:
    """Recursively scrub strings inside common container shapes one level deep.

    Broker / Slack responses are typically `dict[str, Any]` payloads — we
    redact-by-key AND scan their string values too. Lists/tuples of strings
    are also handled (Bearer headers, etc.).
    """
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, Mapping):
        # Walk dict values; if a key matches _REDACT_KEYS, fully blank the value.
        scrubbed: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _REDACT_KEYS:
                scrubbed[k] = "<REDACTED>"
            else:
                scrubbed[k] = _scrub_value(v)
        return scrubbed
    if isinstance(value, (list, tuple)):
        scrubbed_seq = [_scrub_value(v) for v in value]
        return type(value)(scrubbed_seq) if isinstance(value, tuple) else scrubbed_seq
    return value


def _redact(
    _logger: Any,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor — must run BEFORE the JSONRenderer.

    1. Key-level redaction: any top-level key whose lowercase name is in
       `_REDACT_KEYS` has its value replaced with the literal string
       ``"<REDACTED>"`` regardless of original type.
    2. Value-level redaction: every remaining string value (including the
       free-form `event` message) is scanned for credential-shaped patterns
       and rewritten. Nested mappings / sequences are scrubbed recursively.
    """
    # Phase 1: key-name redaction (defends against `log.info(api_key=...)`).
    for key in list(event_dict.keys()):
        if isinstance(key, str) and key.lower() in _REDACT_KEYS:
            event_dict[key] = "<REDACTED>"

    # Phase 2: value-pattern redaction (defends against tokens embedded in
    # messages or nested dicts).
    for key, value in list(event_dict.items()):
        # Skip values that we already wiped by key — they're already "<REDACTED>".
        if isinstance(key, str) and key.lower() in _REDACT_KEYS:
            continue
        event_dict[key] = _scrub_value(value)

    return event_dict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _level_to_int(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return logging.getLevelNamesMapping().get(level.upper(), logging.INFO)


def configure_logging(level: str | int = "INFO") -> None:
    """Configure structlog with the production processor chain.

    Args:
        level: Either an int (logging.INFO) or a name ("INFO", "DEBUG"...).

    Idempotent: safe to call multiple times. Tests call it on every fixture
    invocation to reset state.
    """
    level_int = _level_to_int(level)

    # Mirror to stdlib logging so anything that uses `logging.getLogger(...)`
    # (e.g., third-party libs) also lands in our handler chain. We DO NOT
    # use ProcessorFormatter here — keeping it simple and routing every
    # event through structlog's own processors keeps the redaction guarantee
    # single-sourced (only `_redact` decides what's safe to emit).
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level_int,
        force=False,  # don't tear down test-installed handlers
    )
    # Adjust root level even when basicConfig is a no-op (called twice).
    logging.getLogger().setLevel(level_int)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_logger_name,
            _redact,  # MUST run before JSONRenderer (AUTH-04)
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # tests reconfigure between fixtures
    )


def get_logger(name: str | None = None) -> Any:
    """Return a structlog BoundLogger.

    Returns Any because structlog's BoundLogger generic is hard to type cleanly
    across versions (structlog.stdlib.BoundLogger vs the dynamic protocol).
    Callers should treat this as `structlog.stdlib.BoundLogger`.
    """
    return structlog.get_logger(name) if name else structlog.get_logger()


__all__: tuple[str, ...] = ("configure_logging", "get_logger")
