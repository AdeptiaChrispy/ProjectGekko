"""Project Gekko CLI — Typer app exposing the ``gekko`` console script.

Plan 01-09 Task 1 closed the stubs left by Plan 01-01. Real subcommands:

  * ``gekko doctor`` — env audit (Plan 01-01).
  * ``gekko init`` — first-run wizard: SQLCipher passphrase prompt + REG-02
    user agreement gate + alembic migration + User row insert with
    ``agreement_acknowledged_at``.
  * ``gekko serve`` — start the FastAPI dashboard + Slack adapter +
    APScheduler in one uvicorn process. Prompts for the SQLCipher
    passphrase at startup and caches it via :mod:`gekko.vault.passphrase`.
  * ``gekko run <strategy>`` — D-06 CLI trigger surface. Calls
    :func:`gekko.agent.runtime.trigger_strategy_run` with ``source="cli"``.
  * ``gekko strategy create`` — D-04 author surface. Two mutually-exclusive
    modes: flag-mode (``--name/--thesis/--watchlist`` + hard-cap flags)
    OR chat-mode (``--from-chat`` reads NL transcript from stdin and
    pipes it through :func:`gekko.agent.runtime.compile_strategy_from_chat`
    per STRAT-01).
  * ``gekko audit verify`` — :func:`gekko.audit.verify.walk_chain` over the
    current user's events; reports intact / broken.
  * ``gekko audit dump --limit N`` — print the last N events as JSON.

The Typer app variable MUST be named ``app`` — it is the entry referenced
by ``[project.scripts] gekko = "gekko.cli:app"`` in pyproject.toml.

Process-bootstrap invariant: every command that touches the encrypted DB
calls :func:`gekko.vault.passphrase.prompt_passphrase` (or expects
:func:`set_passphrase` to have been called via env) BEFORE building any
engine. The runtime / executor / slack handler all read the cached
passphrase via :func:`gekko.vault.passphrase.get_passphrase`.

Per Pitfall 11 (uvicorn ``--reload`` + APScheduler): ``gekko serve``
runs uvicorn with a single worker and NO reload flag. The docstring
documents this loudly so a future hot-fix doesn't re-introduce the
duplicate-scheduler-fires bug.
"""

from __future__ import annotations

import asyncio
import getpass
import importlib
import json as _json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import typer

if TYPE_CHECKING:  # pragma: no cover
    pass

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
# `gekko doctor` — Plan 01-01 (unchanged surface; AUTH-04 redaction)
# ---------------------------------------------------------------------------


REQUIRED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
)

OPTIONAL_ENV_VARS = (
    "SLACK_USER_ID",
    "FINNHUB_API_KEY",
)


@dataclass
class CheckResult:
    """Outcome of a single doctor check (no credential values are stored)."""

    name: str
    ok: bool
    required: bool
    detail: str = ""


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info.major, sys.version_info.minor
    ok = major == 3 and minor == 12
    return CheckResult(
        name=f"Python 3.12.x (running {major}.{minor}.{sys.version_info.micro})",
        ok=ok,
        required=True,
        detail="" if ok else "Phase 1 requires Python 3.12 per D-18.",
    )


def _check_env_var(name: str, *, required: bool) -> CheckResult:
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
        return CheckResult(
            name=f"import {module_name}", ok=True, required=required, detail="ok"
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name=f"import {module_name}",
            ok=False,
            required=required,
            detail=f"FAILED ({type(exc).__name__})",
        )


def _check_tzdata_zoneinfo() -> CheckResult:
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

    Exits 0 if all REQUIRED checks pass, exits 1 otherwise. NEVER echoes
    env-var values (AUTH-04).

    Loads ``.env`` (if present in cwd) BEFORE checking — pydantic-settings
    does the same when constructing the live Settings object, so this
    keeps doctor's diagnostic view consistent with what the rest of the
    process sees.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)
    results = _run_doctor_checks()

    typer.echo("gekko doctor — environment audit")
    typer.echo("=" * 60)

    failed_required = 0
    for r in results:
        tag = "REQ" if r.required else "OPT"
        status = (
            "ok "
            if r.ok
            else ("MISSING" if not r.required else "MISSING (required)")
        )
        typer.echo(f"  [{tag}] {r.name:<55} {r.detail or status}")
        if r.required and not r.ok:
            failed_required += 1

    typer.echo("=" * 60)
    if failed_required:
        typer.echo(f"FAIL: {failed_required} required check(s) missing.")
        raise typer.Exit(code=1)
    typer.echo("OK: all required checks passed.")


# ---------------------------------------------------------------------------
# `gekko init` — first-run wizard (REG-02)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@app.command("init")
def init() -> None:
    """First-run wizard: passphrase + user agreement (REG-02) + DB init.

    Three gates the operator must pass:

      1. **Passphrase confirmation.** Two ``getpass`` prompts must match.
         The passphrase is cached via :mod:`gekko.vault.passphrase` and is
         never persisted to disk (D-19).
      2. **REG-02 user agreement.** Display the agreement text; require
         the operator to type exactly ``I agree`` (case-insensitive) to
         proceed.
      3. **Database setup.** Run ``alembic upgrade head`` against the
         per-user SQLCipher DB; insert a User row with
         ``agreement_acknowledged_at`` populated.
    """
    from gekko.config import get_settings
    from gekko.dashboard.templates import USER_AGREEMENT_TEXT
    from gekko.db.engine import get_async_engine
    from gekko.db.models import User
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import set_passphrase

    configure_logging()
    settings = get_settings()

    typer.echo("Welcome to Gekko. Setting up your encrypted local database.")

    passphrase = getpass.getpass(
        "Choose a SQLCipher passphrase (cannot be recovered): "
    )
    passphrase_confirm = getpass.getpass("Confirm passphrase: ")
    if passphrase != passphrase_confirm:
        typer.echo("Passphrases did not match. Aborting.")
        raise typer.Exit(code=1)
    set_passphrase(passphrase)

    typer.echo("")
    typer.echo(USER_AGREEMENT_TEXT)
    ack = typer.prompt('Type "I agree" to acknowledge')
    if ack.strip().lower() != "i agree":
        typer.echo("Agreement not acknowledged. Aborting.")
        raise typer.Exit(code=1)

    db_path = settings.db_path_for(settings.gekko_user_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Run migrations via subprocess so alembic env.py picks up the
    # passphrase from env (Plan 01-03 contract).
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={
            **os.environ,
            "GEKKO_DB_PASSPHRASE": passphrase,
            "GEKKO_USER_ID": settings.gekko_user_id,
        },
    )

    # Insert User row with agreement_acknowledged_at.
    async def _insert_user() -> None:
        engine = get_async_engine(db_path, passphrase)
        try:
            now = _now_iso()
            async with make_session_factory(engine)() as session, session.begin():
                session.add(
                    User(
                        user_id=settings.gekko_user_id,
                        created_at=now,
                        agreement_acknowledged_at=now,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(_insert_user())

    typer.echo(
        "Initialized. Run `gekko strategy create` to author your first strategy."
    )


# ---------------------------------------------------------------------------
# `gekko serve` — FastAPI + Slack + scheduler
# ---------------------------------------------------------------------------


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host (default loopback)."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Start FastAPI dashboard + Slack adapter + APScheduler.

    Prompts for the SQLCipher passphrase at startup and caches it via
    :mod:`gekko.vault.passphrase`. uvicorn runs with a single worker and
    no reload flag — Pitfall 11 (multiple workers / reload causes
    duplicate scheduler fires).
    """
    import uvicorn

    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import prompt_passphrase

    configure_logging()
    prompt_passphrase("Enter SQLCipher passphrase to unlock DB: ")

    # Late import to defer FastAPI app construction until passphrase is set.
    from gekko.dashboard.app import create_app

    uvicorn.run(create_app(), host=host, port=port, workers=1)


# ---------------------------------------------------------------------------
# `gekko run <strategy>` — CLI trigger surface (D-06)
# ---------------------------------------------------------------------------


@app.command("run")
def run(strategy_name: str = typer.Argument(..., help="Strategy slug to run.")) -> None:
    """Trigger a one-shot strategy run (D-06 CLI surface).

    Awaits the agent run AND posts the HITL-01 Block Kit card to the
    operator's Slack DM before returning. (The slash command + dashboard
    route fire-and-forget; the CLI is interactive and exits when the
    chain is at the user-approval boundary.)
    """
    from gekko.agent.runtime import trigger_strategy_run
    from gekko.config import get_settings
    from gekko.logging_config import configure_logging
    from gekko.reporter.slack import post_run_result
    from gekko.vault.passphrase import prompt_passphrase

    configure_logging()
    prompt_passphrase()
    settings = get_settings()

    async def _run_and_post() -> dict[str, Any]:
        r = await trigger_strategy_run(
            user_id=settings.gekko_user_id,
            strategy_name=strategy_name,
            source="cli",
        )
        try:
            await post_run_result(settings.gekko_user_id, r)
        except Exception as exc:
            typer.echo(
                f"WARN: agent run completed but posting the Slack card "
                f"failed: {exc}",
                err=True,
            )
        return r

    result = asyncio.run(_run_and_post())
    typer.echo(
        f"Triggered {strategy_name} "
        f"(run_id={result['run_id']}, outcome={result['outcome']}) "
        "— check Slack for the proposal card."
    )


# ---------------------------------------------------------------------------
# `gekko strategy create` — flag mode AND chat mode (STRAT-01)
# ---------------------------------------------------------------------------


@strategy_app.command("create")
def strategy_create(
    name: str | None = typer.Option(None, help="Strategy slug."),
    thesis: str | None = typer.Option(None, help="Plain-English thesis."),
    watchlist: str | None = typer.Option(
        None, help="Comma-separated tickers (e.g. NVDA,AMD,AVGO)."
    ),
    max_position_pct: float = typer.Option(0.05, help="Max position size (0..0.20)."),
    max_daily_loss_usd: float = typer.Option(200, help="Max daily loss in USD."),
    max_trades_per_day: int = typer.Option(3, help="Max trades per day."),
    max_sector_exposure_pct: float = typer.Option(
        0.25, help="Max sector exposure."
    ),
    mode: str = typer.Option("paper", help="paper or live (P1: paper only)."),
    schedule_time: str | None = typer.Option(
        None, help="Daily fire schedule, e.g. '10:00 America/New_York'."
    ),
    from_chat: bool = typer.Option(
        False,
        "--from-chat",
        help=(
            "Read a natural-language strategy description from stdin "
            "(EOF-terminated) and compile via "
            "compile_strategy_from_chat (STRAT-01). Mutually exclusive "
            "with --name/--thesis/--watchlist."
        ),
    ),
) -> None:
    """Create or update a strategy (D-05 snapshot row).

    Two mutually-exclusive input modes:

      * **Flag mode** (default): requires ``--name``, ``--thesis``, and
        ``--watchlist`` — hard caps default to safe P1 values.
      * **Chat mode** (``--from-chat``): reads an NL chat transcript from
        stdin until EOF and runs it through
        :func:`gekko.agent.runtime.compile_strategy_from_chat` to produce
        a validated Strategy.

    Both modes converge on the same insert path —
    :func:`gekko.schemas.strategy.next_version` assigns the new version
    number scoped to ``(user_id, strategy_name)``.
    """
    from gekko.agent.runtime import compile_strategy_from_chat
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.models import Strategy as StrategyRow
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.schemas.strategy import HardCaps, Strategy, next_version
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    flag_mode_supplied = any(v is not None for v in (name, thesis, watchlist))
    if from_chat and flag_mode_supplied:
        typer.echo(
            "ERROR: --from-chat is mutually exclusive with "
            "--name/--thesis/--watchlist. Pass one mode or the other.",
            err=True,
        )
        raise typer.Exit(code=2)
    if not from_chat and not (name and thesis and watchlist):
        typer.echo(
            "ERROR: flag mode requires --name, --thesis, and --watchlist. "
            "Or pass --from-chat to read a chat transcript from stdin.",
            err=True,
        )
        raise typer.Exit(code=2)

    # Passphrase must be set BEFORE we build an engine. Tests preset via
    # set_passphrase; production prompts on the TTY.
    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    if from_chat:
        chat_transcript = sys.stdin.read()
        if not chat_transcript.strip():
            typer.echo(
                "ERROR: --from-chat requires a non-empty transcript on stdin.",
                err=True,
            )
            raise typer.Exit(code=2)
        strategy = asyncio.run(
            compile_strategy_from_chat(
                user_id=settings.gekko_user_id,
                chat_transcript=chat_transcript,
            )
        )
        strategy = strategy.model_copy(
            update={
                "user_id": settings.gekko_user_id,
                "created_by_chat": True,
                "created_at": _now_iso(),
            }
        )
        resolved_name = strategy.name
    else:
        # name/thesis/watchlist are guaranteed non-None by the guard above.
        assert name is not None and thesis is not None and watchlist is not None
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
        strategy = Strategy(
            user_id=settings.gekko_user_id,
            name=name,
            version=1,
            thesis=thesis,
            watchlist=tickers,
            hard_caps=HardCaps(
                max_position_pct=Decimal(str(max_position_pct)),
                max_daily_loss_usd=Decimal(str(max_daily_loss_usd)),
                max_trades_per_day=max_trades_per_day,
                max_sector_exposure_pct=Decimal(str(max_sector_exposure_pct)),
            ),
            mode=mode,
            schedule_time=schedule_time,
            created_at=_now_iso(),
            created_by_chat=False,
        )
        resolved_name = name

    async def _save() -> int:
        engine = get_async_engine(
            settings.db_path_for(settings.gekko_user_id),
            get_passphrase(),
        )
        try:
            async with make_session_factory(engine)() as session, session.begin():
                v = await next_version(
                    session,
                    user_id=settings.gekko_user_id,
                    strategy_name=resolved_name,
                )
                versioned = strategy.model_copy(update={"version": v})
                session.add(
                    StrategyRow(
                        strategy_id="strat-" + uuid4().hex,
                        user_id=settings.gekko_user_id,
                        strategy_name=resolved_name,
                        version=v,
                        payload_json=versioned.model_dump_json(),
                        created_at=versioned.created_at,
                    )
                )
                return v
        finally:
            await engine.dispose()

    v = asyncio.run(_save())
    typer.echo(
        f"Saved strategy {resolved_name} v{v} "
        f"(mode={'chat' if from_chat else 'flag'})"
    )


# ---------------------------------------------------------------------------
# `gekko audit verify` / `dump`
# ---------------------------------------------------------------------------


@audit_app.command("verify")
def audit_verify(
    user_id: str | None = typer.Option(
        None, help="Override user_id (default: settings.gekko_user_id)."
    ),
) -> None:
    """Walk the SHA-256 hash chain and report intact / broken row IDs."""
    from sqlalchemy import select

    from gekko.audit.verify import walk_chain
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.models import Event as EventRow
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()
    resolved = user_id or settings.gekko_user_id

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    async def _walk() -> tuple[list[int], int]:
        engine = get_async_engine(
            settings.db_path_for(resolved), get_passphrase()
        )
        try:
            async with make_session_factory(engine)() as session:
                breaks = await walk_chain(session, resolved)
                count = (
                    await session.execute(
                        select(EventRow).where(EventRow.user_id == resolved)
                    )
                ).scalars().all()
                return breaks, len(count)
        finally:
            await engine.dispose()

    breaks, count = asyncio.run(_walk())
    if not breaks:
        typer.echo(f"Chain intact across {count} events for user {resolved}")
    else:
        typer.echo(
            f"Chain BROKEN at row(s): {breaks} (user {resolved})",
            err=True,
        )
        raise typer.Exit(code=1)


@audit_app.command("dump")
def audit_dump(
    limit: int = typer.Option(5, help="Number of most recent events to print."),
    user_id: str | None = typer.Option(
        None, help="Override user_id (default: settings.gekko_user_id)."
    ),
) -> None:
    """Print the most recent N audit events as line-delimited JSON."""
    from sqlalchemy import select

    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.models import Event as EventRow
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()
    resolved = user_id or settings.gekko_user_id

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    async def _dump() -> list[EventRow]:
        engine = get_async_engine(
            settings.db_path_for(resolved), get_passphrase()
        )
        try:
            async with make_session_factory(engine)() as session:
                q = (
                    select(EventRow)
                    .where(EventRow.user_id == resolved)
                    .order_by(EventRow.id.desc())
                    .limit(limit)
                )
                return list((await session.execute(q)).scalars().all())
        finally:
            await engine.dispose()

    rows = asyncio.run(_dump())
    for row in rows:
        try:
            payload = _json.loads(row.payload_json)
        except _json.JSONDecodeError:
            payload = {"_raw": row.payload_json}
        typer.echo(
            _json.dumps(
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "ts": row.ts,
                    "prev_hash": row.prev_hash,
                    "row_hash": row.row_hash,
                    "payload": payload,
                }
            )
        )


if __name__ == "__main__":
    app()
