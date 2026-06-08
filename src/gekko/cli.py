"""Project Gekko CLI — Typer app exposing the `gekko` console script.

This is the scaffolding stub created by Plan 01-01 Task 3. Real implementations
of `doctor` land in Task 4; `init`, `serve`, `run`, `strategy create`,
`audit verify`, and `audit dump` are filled in by later plans (mostly 01-09).

The Typer app variable MUST be named `app` — it is the entry referenced by
`[project.scripts] gekko = "gekko.cli:app"` in pyproject.toml and by
`src/gekko/__main__.py` for `python -m gekko`.
"""

from __future__ import annotations

import typer


app = typer.Typer(
    name="gekko",
    help="Project Gekko — autonomous Claude-powered stock trading agent.",
    no_args_is_help=True,
    add_completion=False,
)

strategy_app = typer.Typer(
    name="strategy",
    help="Manage trading strategies (create / edit / list).",
    no_args_is_help=True,
)
app.add_typer(strategy_app, name="strategy")

audit_app = typer.Typer(
    name="audit",
    help="Inspect the SHA-256-chained audit log.",
    no_args_is_help=True,
)
app.add_typer(audit_app, name="audit")


# ---------------------------------------------------------------------------
# Top-level commands (stubs — real implementations land in later plans)
# ---------------------------------------------------------------------------


@app.command("init")
def init() -> None:
    """First-run wizard — collects passphrase, broker/Slack/Anthropic credentials.

    Real implementation: Plan 01-09 Task 1 (REG-02 user-agreement gate).
    """
    typer.echo("TODO: first-run wizard (01-09)")


@app.command("serve")
def serve() -> None:
    """Start the FastAPI dashboard + Slack listener + APScheduler in-process.

    Real implementation: Plan 01-09 (orchestrator wiring).
    """
    typer.echo("TODO: start FastAPI + Slack + scheduler (01-09)")


@app.command("run")
def run(strategy_name: str) -> None:
    """Trigger a one-shot strategy run by name.

    Real implementation: Plan 01-09 Task 1 — calls `trigger_strategy_run`.
    """
    typer.echo(f"TODO: trigger strategy run for '{strategy_name}' (01-09)")


@app.command("doctor")
def doctor() -> None:
    """Env-audit: report PRESENT/MISSING for required deps and credentials.

    NOTE: This is the stub from Task 3. Task 4 of Plan 01-01 replaces it with
    the real implementation (Python version + env-var presence + tzdata +
    sqlcipher3 import) that NEVER echoes credential values (AUTH-04).
    """
    typer.echo("TODO: env audit (01-01 Task 4)")


# ---------------------------------------------------------------------------
# `gekko strategy ...` subcommand group
# ---------------------------------------------------------------------------


@strategy_app.command("create")
def strategy_create() -> None:
    """Create a new trading strategy.

    Real implementation: Plan 01-09 Task 1 (NL-chat + form modes per D-04).
    """
    typer.echo("TODO: create strategy (01-09)")


# ---------------------------------------------------------------------------
# `gekko audit ...` subcommand group
# ---------------------------------------------------------------------------


@audit_app.command("verify")
def audit_verify() -> None:
    """Walk the SHA-256 hash chain end-to-end and report any breaks.

    Real implementation: Plan 01-04 (walk_chain) + Plan 01-09 (CLI binding).
    """
    typer.echo("TODO: audit chain verification (01-04 / 01-09)")


@audit_app.command("dump")
def audit_dump() -> None:
    """Dump audit events for a user as JSON.

    Real implementation: Plan 01-09.
    """
    typer.echo("TODO: audit chain dump (01-04 / 01-09)")


if __name__ == "__main__":
    app()
