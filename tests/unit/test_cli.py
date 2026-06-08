"""Plan 01-01 Task 4 — Typer CLI smoke tests + `gekko doctor` redaction.

Implements VALIDATION.md rows 1 (`doctor` env-audit) and 2 (`gekko --help`
shows commands). The redaction test enforces AUTH-04 — env-var values must
never appear in any CLI output path.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from gekko.cli import app

runner = CliRunner()


def test_help_smoke() -> None:
    """`gekko --help` exits 0 and lists every subcommand we promised."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    # VALIDATION.md row 2 — these six commands must appear.
    for cmd in ("init", "serve", "run", "doctor", "strategy", "audit"):
        assert cmd in result.output, f"missing command in --help output: {cmd!r}"


def test_doctor_missing_envvar_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a required env-var is unset, doctor exits nonzero with a MISSING line."""
    # Strip every required env-var so the failure is unambiguous.
    for var in (
        "ANTHROPIC_API_KEY",
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET_KEY",
        "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0, "doctor must exit nonzero when required envs missing"
    assert "ANTHROPIC_API_KEY" in result.output
    assert "MISSING" in result.output


def test_doctor_redacts_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH-04: `gekko doctor` MUST NOT echo any credential value.

    We seed a unique sentinel into every required env-var; if any of those
    strings show up in doctor's output, the redaction guarantee is broken.
    """
    sentinels = {
        "ANTHROPIC_API_KEY": "secret-anthropic-xyz-001",
        "ALPACA_PAPER_API_KEY": "secret-alpaca-key-xyz-002",
        "ALPACA_PAPER_SECRET_KEY": "secret-alpaca-secret-xyz-003",
        "SLACK_BOT_TOKEN": "xoxb-secret-bot-token-xyz-004",
        "SLACK_SIGNING_SECRET": "secret-slack-signing-xyz-005",
        "SLACK_USER_ID": "U-secret-user-xyz-006",
        "FINNHUB_API_KEY": "secret-finnhub-xyz-007",
    }
    for var, value in sentinels.items():
        monkeypatch.setenv(var, value)

    result = runner.invoke(app, ["doctor"])
    # With every required env-var set, doctor should exit 0 (sqlcipher3+tzdata
    # are already installed). But what we strictly care about here is redaction
    # regardless of exit code.
    for var, value in sentinels.items():
        assert value not in result.output, (
            f"AUTH-04 violation: doctor echoed credential value for {var}"
        )
