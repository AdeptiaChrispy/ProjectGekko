"""Plan 01-02 Task 1 — structlog credential-redaction processor tests (AUTH-04, D-25).

Per VALIDATION.md row `01-02-T1`: every known credential shape Phase 1 will encounter
must be redacted at the structlog processor layer before JSON serialization.

Covered patterns:
  * Bearer JWT-style tokens
  * `sk-...` (OpenAI/generic)
  * `sk-ant-...` (Anthropic — explicit, even though sk- catches it)
  * `PK...` (Alpaca live key shape; paper keys also begin with PK)
  * `xoxb-...` (Slack bot OAuth)
  * `xapp-...` (Slack app-level)
  * `xoxa-...` (Slack user OAuth — added by executor; see SUMMARY)
  * Dict-shaped keys named api_key / secret_key / passphrase / password /
    token / authorization / slack_token / client_secret (case-insensitive)

Every test reconfigures structlog from scratch via `configure_logging()` so the
processor chain under test is the one we ship.
"""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from gekko.logging_config import configure_logging, get_logger

# ---------------------------------------------------------------------------
# Helpers — capture rendered JSON output
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_log_output() -> io.StringIO:
    """Reconfigure structlog so every record is rendered to a fresh StringIO.

    We bind a dedicated stdlib logger that writes to the StringIO and reset
    structlog to its production processor chain (with redaction active).
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    # Remove any handlers from prior tests, attach our capture handler.
    prior = list(root.handlers)
    for h in prior:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    configure_logging(level="DEBUG")

    yield stream

    # Restore prior handlers (best-effort cleanup).
    root.removeHandler(handler)
    for h in prior:
        root.addHandler(h)


def _last_line(stream: io.StringIO) -> str:
    text = stream.getvalue()
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines, f"no log line was captured; full stream={text!r}"
    return lines[-1]


def _last_record(stream: io.StringIO) -> dict:
    return json.loads(_last_line(stream))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_key_named_api_key_value_is_redacted(captured_log_output: io.StringIO) -> None:
    """A log call with `api_key="actual-secret-value"` redacts the value entirely."""
    log = get_logger("test.api_key")
    log.info("calling broker", api_key="actual-secret-value")

    line = _last_line(captured_log_output)
    record = json.loads(line)

    assert record["api_key"] == "<REDACTED>"
    assert "actual-secret-value" not in line, (
        "AUTH-04: literal secret value leaked into log output"
    )


def test_bearer_token_in_message_is_redacted(captured_log_output: io.StringIO) -> None:
    """A free-form message containing `Bearer eyJ...` is rewritten to `Bearer <REDACTED>`."""
    log = get_logger("test.bearer")
    log.info("upstream returned Bearer eyJhbGciOiJI.UzI1NiIs.JWT-token-XYZ-payload-here")

    line = _last_line(captured_log_output)
    assert "Bearer <REDACTED>" in line
    assert "eyJhbGciOiJI" not in line
    assert "JWT-token-XYZ" not in line


def test_alpaca_pk_key_pattern_is_redacted(captured_log_output: io.StringIO) -> None:
    """Alpaca-shaped key `PK1234567890ABCDEFGHIJ` -> `<REDACTED-ALPACA>`."""
    log = get_logger("test.alpaca")
    log.info("placed order with PK1234567890ABCDEFGHIJ")

    line = _last_line(captured_log_output)
    assert "<REDACTED-ALPACA>" in line
    assert "PK1234567890ABCDEFGHIJ" not in line


def test_slack_bot_token_xoxb_is_redacted(captured_log_output: io.StringIO) -> None:
    """`xoxb-1234-5678-abcdef` -> `<REDACTED-XOXB>`."""
    log = get_logger("test.xoxb")
    log.info("posting via xoxb-1234-5678-abcdef-token")

    line = _last_line(captured_log_output)
    assert "<REDACTED-XOXB>" in line
    assert "xoxb-1234-5678" not in line


def test_slack_app_token_xapp_is_redacted(captured_log_output: io.StringIO) -> None:
    """`xapp-1-A123-1234-abcdef` -> `<REDACTED-XAPP>`."""
    log = get_logger("test.xapp")
    log.info("socket mode token xapp-1-A123-1234-abcdef-tail")

    line = _last_line(captured_log_output)
    assert "<REDACTED-XAPP>" in line
    assert "xapp-1-A123" not in line


def test_openai_sk_key_is_redacted(captured_log_output: io.StringIO) -> None:
    """`sk-1234567890abcdef1234567890` -> `<REDACTED-SK>`."""
    log = get_logger("test.sk")
    log.info("calling llm with sk-1234567890abcdef1234567890ABCD")

    line = _last_line(captured_log_output)
    assert "<REDACTED-SK>" in line
    assert "sk-1234567890abcdef" not in line


def test_anthropic_sk_ant_key_is_redacted(captured_log_output: io.StringIO) -> None:
    """`sk-ant-...` Anthropic shape is redacted (explicit pattern + sk- fallback)."""
    log = get_logger("test.sk_ant")
    log.info("calling anthropic with sk-ant-api03-deadbeefDEADBEEF1234567890XYZ-tail")

    line = _last_line(captured_log_output)
    # The exact replacement is either <REDACTED-ANTHROPIC> or <REDACTED-SK> —
    # but the raw token MUST NOT appear in the output.
    assert "deadbeefDEADBEEF" not in line
    assert "sk-ant-api03" not in line


def test_named_secret_keys_are_redacted_regardless_of_value(
    captured_log_output: io.StringIO,
) -> None:
    """Dict-shaped key-name redaction works for the full _REDACT_KEYS set."""
    log = get_logger("test.named_keys")
    log.info(
        "credential bundle",
        password="hunter2",
        passphrase="correct-horse-battery-staple",
        token="opaque-token-zzz",
        authorization="Basic ZmFrZQ==",
        slack_token="xoxb-but-not-pattern-matched",
        client_secret="oauth-shared-secret",
        secret_key="raw-secret-value",
    )

    record = _last_record(captured_log_output)
    for k in (
        "password",
        "passphrase",
        "token",
        "authorization",
        "slack_token",
        "client_secret",
        "secret_key",
    ):
        assert record[k] == "<REDACTED>", f"{k} was not redacted by name; got {record[k]!r}"

    line = _last_line(captured_log_output)
    for raw_value in (
        "hunter2",
        "correct-horse-battery-staple",
        "opaque-token-zzz",
        "ZmFrZQ==",
        "oauth-shared-secret",
        "raw-secret-value",
    ):
        assert raw_value not in line, f"AUTH-04: {raw_value!r} leaked into log output"


def test_output_is_valid_json(captured_log_output: io.StringIO) -> None:
    """JSONRenderer is the LAST processor; every line is parseable JSON."""
    log = get_logger("test.json")
    log.info("structured", foo="bar", qty=3, ratio=0.5)

    record = _last_record(captured_log_output)
    assert record["foo"] == "bar"
    assert record["qty"] == 3
    assert record["ratio"] == 0.5
    assert record["event"] == "structured"


def test_timestamp_is_iso8601_utc_and_log_level_present(
    captured_log_output: io.StringIO,
) -> None:
    """TimeStamper(fmt='iso', utc=True) + add_log_level produce the expected fields."""
    log = get_logger("test.meta")
    log.warning("hello", extra=1)

    record = _last_record(captured_log_output)
    assert "timestamp" in record, f"no timestamp in record: {record}"
    # ISO 8601 UTC: ends with 'Z' or '+00:00'
    ts = record["timestamp"]
    assert re.search(r"(Z|\+00:00)$", ts), f"timestamp is not UTC-ISO 8601: {ts!r}"
    assert record["level"] in ("warning", "WARNING"), (
        f"add_log_level not active; record={record}"
    )
