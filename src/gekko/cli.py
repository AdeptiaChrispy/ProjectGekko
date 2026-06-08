"""Project Gekko CLI — Typer app exposing the `gekko` console script.

This is the scaffolding created by Plan 01-01.
- Task 3 created the stub app and command surface.
- Task 4 wires the real `gekko doctor` env-audit subcommand per VALIDATION row 1
  (PRESENT/MISSING credential reporting that NEVER echoes values — AUTH-04
  redaction; verified by `tests/unit/test_cli.py::test_doctor_redacts_values`).
- Other commands (`init`, `serve`, `run`, `strategy create`, `audit verify`,
  `audit dump`) remain stubs — real implementations land in Plan 01-09.

The Typer app variable MUST be named `app` — it is the entry referenced by
`[project.scripts] gekko = "gekko.cli:app"` in pyproject.toml and by
`src/gekko/__main__.py` for `python -m gekko`.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass

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
# `gekko doctor` — real env-audit (Plan 01-01 Task 4)
#
# Per AUTH-04 (D-25) credential redaction: this command NEVER echoes the
# value of any env var. It only prints PRESENT / MISSING flags. The redaction
# guarantee is verified by tests/unit/test_cli.py::test_doctor_redacts_values.
# ---------------------------------------------------------------------------


REQUIRED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
)

OPTIONAL_ENV_VARS = (
    "SLACK_USER_ID",   # required for DMing the user but P1 can degrade
    "FINNHUB_API_KEY", # RES-02 news evidence; graceful-degrade per RESEARCH
)


@dataclass
class CheckResult:
    """Outcome of a single doctor check (no credential values are stored)."""

    name: str
    ok: bool
    required: bool
    detail: str = ""  # human-readable but MUST NOT contain secret values


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info.major, sys.version_info.minor
    ok = (major == 3 and minor == 12)
    return CheckResult(
        name=f"Python 3.12.x (running {major}.{minor}.{sys.version_info.micro})",
        ok=ok,
        required=True,
        detail="" if ok else "Phase 1 requires Python 3.12 per D-18.",
    )


def _check_env_var(name: str, *, required: bool) -> CheckResult:
    """Check env-var presence WITHOUT echoing its value (AUTH-04)."""
    present = bool(os.environ.get(name, "").strip())
    return CheckResult(
        name=name,
        ok=present,
        required=required,
        detail="PRESENT" if present else "MISSING",
    )


def _check_importable(module_name: str, *, required: bool) -> CheckResult:
    try:
        importlib.import_module(module_name)
        return CheckResult(name=f"import {module_name}", ok=True, required=required, detail="ok")
    except Exception as exc:  # noqa: BLE001 — broad except is intentional for doctor
        # Note: `exc` may contain a path but never a credential.
        return CheckResult(
            name=f"import {module_name}",
            ok=False,
            required=required,
            detail=f"FAILED ({type(exc).__name__})",
        )


def _check_tzdata_zoneinfo() -> CheckResult:
    """Verify Windows tzdata gotcha (Pitfall 5): IANA tz lookups must succeed."""
    try:
        import zoneinfo

        zoneinfo.ZoneInfo("America/New_York")
        return CheckResult(
            name="zoneinfo America/New_York",
            ok=True,
            required=True,
            detail="ok",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="zoneinfo America/New_York",
            ok=False,
            required=True,
            detail=f"FAILED ({type(exc).__name__}) — install `tzdata` package on Windows",
        )


def _run_doctor_checks() -> list[CheckResult]:
    results: list[CheckResult] = [_check_python_version()]
    results.extend(_check_env_var(var, required=True) for var in REQUIRED_ENV_VARS)
    results.extend(_check_env_var(var, required=False) for var in OPTIONAL_ENV_VARS)
    results.append(_check_tzdata_zoneinfo())
    results.append(_check_importable("sqlcipher3", required=True))
    return results


@app.command("doctor")
def doctor() -> None:
    """Env-audit: report PRESENT/MISSING for required deps and credentials.

    Exits 0 if all REQUIRED checks pass, exits 1 otherwise. Optional checks
    (FINNHUB_API_KEY, SLACK_USER_ID) print a warning but do not fail.

    NEVER echoes env-var values (AUTH-04).
    """
    results = _run_doctor_checks()

    typer.echo("gekko doctor — environment audit")
    typer.echo("=" * 60)

    failed_required = 0
    for r in results:
        tag = "REQ" if r.required else "OPT"
        status = "ok " if r.ok else ("MISSING" if not r.required else "MISSING (required)")
        # The detail string is bounded above to contain only constants —
        # specifically "PRESENT" / "MISSING" / "ok" / "FAILED (ExceptionName)".
        # No env-var value is ever interpolated here.
        typer.echo(f"  [{tag}] {r.name:<55} {r.detail or status}")
        if r.required and not r.ok:
            failed_required += 1

    typer.echo("=" * 60)
    if failed_required:
        typer.echo(f"FAIL: {failed_required} required check(s) missing.")
        raise typer.Exit(code=1)
    typer.echo("OK: all required checks passed.")


# ---------------------------------------------------------------------------
# Stubs — real implementations land in later plans
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


@strategy_app.command("create")
def strategy_create() -> None:
    """Create a new trading strategy.

    Real implementation: Plan 01-09 Task 1 (NL-chat + form modes per D-04).
    """
    typer.echo("TODO: create strategy (01-09)")


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
