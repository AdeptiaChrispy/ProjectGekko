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

credentials_app = typer.Typer(
    name="credentials",
    help="Manage broker credentials in the SQLCipher vault (D-34).",
    no_args_is_help=True,
)
app.add_typer(credentials_app, name="credentials")


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
# `gekko kill` / `gekko unkill` — Plan 02-05 Task 2 (D-38 / EXEC-06)
# ---------------------------------------------------------------------------


@app.command("kill")
def kill() -> None:
    """Halt all trading immediately (typed-KILL confirmation).

    Recovery surface when Slack + dashboard are wedged (D-38). The kill
    state is global and persistent — survives process restart per D-36.
    Resume requires explicit ``gekko unkill``.

    Per UI-SPEC §2b copywriting: ``KILL`` is the only single-word CTA
    reserved for the kill switch. Operator must type the literal
    ``KILL`` exactly (uppercase) to confirm.
    """
    from gekko.config import get_settings
    from gekko.execution.kill_switch import _execute_kill
    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    typer.echo("This cancels all open orders across all strategies.")
    typer.echo(
        "Kill state persists across process restarts. "
        "Resume requires `gekko unkill`."
    )
    ack = typer.prompt('Type "KILL" to confirm')
    if ack.strip() != "KILL":
        typer.echo("Aborted — typed confirmation not matched.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    tally = asyncio.run(
        _execute_kill(
            user_id=settings.gekko_user_id, source="cli", reason="manual"
        )
    )
    typer.echo(
        f"Kill ACTIVE. Cancelled {tally['cancelled']}/{tally['total']}; "
        f"{tally.get('pending', 0)} pending; "
        f"{tally.get('failed', 0)} failed."
    )


@app.command("unkill")
def unkill() -> None:
    """Resume trading after a kill (typed-UNKILL confirmation).

    Note: previously-cancelled orders are NOT restored.
    """
    from gekko.config import get_settings
    from gekko.execution.kill_switch import _execute_unkill
    from gekko.logging_config import configure_logging
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    typer.echo(
        "Note: previously-cancelled orders are NOT restored by unkill."
    )
    ack = typer.prompt('Type "UNKILL" to confirm')
    if ack.strip() != "UNKILL":
        typer.echo("Aborted — typed confirmation not matched.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    asyncio.run(
        _execute_unkill(user_id=settings.gekko_user_id, source="cli")
    )
    typer.echo("Kill cleared — new orders will fire again.")


# ---------------------------------------------------------------------------
# `gekko credentials add-alpaca-live` — Plan 02-06 Task 1 (BROK-A-02 / D-34)
# ---------------------------------------------------------------------------


@credentials_app.command("add-alpaca-live")
def credentials_add_alpaca_live() -> None:
    """Store an Alpaca live API key + secret in the SQLCipher vault.

    Live keys NEVER touch .env per D-34. This command is the ONLY ingress
    path for Alpaca live credentials. Prompts via ``typer.prompt(...,
    hide_input=True)`` so the values never echo to the terminal. Writes
    a single :class:`gekko.db.models.BrokerCredential` row with
    ``kind='alpaca_live'``.

    On success prints the next-step nudge pointing the operator at
    ``gekko strategy promote-live <name>``.
    """
    from gekko.config import get_settings
    from gekko.logging_config import configure_logging
    from gekko.vault.credentials import store_live_credentials
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()
    user_id = settings.gekko_user_id

    # Passphrase first — required to unlock the SQLCipher DB.
    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    # NEVER echo the prompt values back to the operator.
    api_key = typer.prompt(
        "Alpaca live API key", hide_input=True
    )
    secret_key = typer.prompt(
        "Alpaca live secret key", hide_input=True
    )

    try:
        asyncio.run(
            store_live_credentials(
                user_id=user_id, api_key=api_key, secret_key=secret_key
            )
        )
    except Exception as exc:  # noqa: BLE001
        typer.echo(
            f"ERROR: failed to store credentials — {type(exc).__name__}: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Live credentials stored for user {user_id}. Use "
        "`gekko strategy promote-live <name>` to enable a strategy "
        "for live trading."
    )


# ---------------------------------------------------------------------------
# `gekko strategy promote-live` / `demote-live` — Plan 02-06 Task 2 (D-31)
# ---------------------------------------------------------------------------


@strategy_app.command("promote-live")
def strategy_promote_live(
    name: str = typer.Argument(..., help="Strategy slug to promote to live."),
) -> None:
    """Promote a paper-mode strategy to live-eligible (D-31).

    UI-SPEC §"Destructive Action Confirmations": requires the operator
    to type the EXACT strategy name to confirm. On confirm, sets
    ``strategy_metadata.live_mode_eligible=True`` +
    ``live_promoted_at=<iso>``. The first live trade per strategy still
    requires the dashboard dual-channel confirm (HITL-06).
    """
    from gekko.config import get_settings
    from gekko.logging_config import configure_logging
    from gekko.strategy.promotion import promote_strategy_to_live
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    typer.echo(
        f"Promoting strategy {name!r} to live-eligible. "
        "First live trade still requires dashboard dual-channel confirm."
    )
    ack = typer.prompt(f"Type the strategy name {name!r} to confirm")
    if ack.strip() != name:
        typer.echo("Aborted — typed confirmation did not match.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    asyncio.run(
        promote_strategy_to_live(
            user_id=settings.gekko_user_id, strategy_name=name
        )
    )
    typer.echo(
        f"Strategy {name!r} is now live-eligible. "
        "Switch its mode to 'live' in the dashboard or via "
        "`gekko strategy create` to author live trades."
    )


@strategy_app.command("demote-live")
def strategy_demote_live(
    name: str = typer.Argument(..., help="Strategy slug to demote from live."),
) -> None:
    """Demote a live-eligible strategy back to paper-only (D-31).

    Sets ``strategy_metadata.live_mode_eligible=False``. Does NOT clear
    ``first_live_trade_confirmed_at`` — once stamped, the per-strategy
    dual-channel gate stays satisfied even if the strategy is re-promoted
    later.
    """
    from gekko.config import get_settings
    from gekko.logging_config import configure_logging
    from gekko.strategy.promotion import demote_strategy_from_live
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    ack = typer.prompt(f"Type the strategy name {name!r} to confirm")
    if ack.strip() != name:
        typer.echo("Aborted — typed confirmation did not match.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    asyncio.run(
        demote_strategy_from_live(
            user_id=settings.gekko_user_id, strategy_name=name
        )
    )
    typer.echo(f"Strategy {name!r} demoted back to paper-only.")


# ---------------------------------------------------------------------------
# `gekko strategy promote-auto` / `demote-auto` / `trust-status`
# Phase 5 Plan 02 — TRUST-01 / D-T04 (CLI parity; NO Slack promote command).
# ---------------------------------------------------------------------------


@strategy_app.command("promote-auto")
def strategy_promote_auto(
    name: str = typer.Argument(
        ..., help="Strategy slug to promote to auto-execute."
    ),
    mode: str = typer.Option(
        "PAPER", help="Account mode whose streak to check (PAPER/LIVE)."
    ),
) -> None:
    """Promote a strategy to auto-execute within caps (TRUST-01).

    Mirrors the dashboard ``POST /strategies/{name}/promote-auto`` route:
    requires a typed-name confirm AND re-checks the clean-approval streak
    server-side (D-T18b) — an ineligible strategy is NEVER promoted; the CLI
    explains which criterion failed instead (SC-5).
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.strategy.streak import compute_clean_streak
    from gekko.strategy.trust import promote_strategy_to_auto
    from gekko.vault.passphrase import (
        get_passphrase,
        prompt_passphrase,
    )

    configure_logging()
    settings = get_settings()
    account_mode = mode.strip().upper()

    typer.echo(
        f"Promoting strategy {name!r} to auto-execute "
        f"({account_mode}). It will place trades without asking first, "
        "within its caps. You can demote any time."
    )
    ack = typer.prompt(f"Type the strategy name {name!r} to confirm")
    if ack.strip() != name:
        typer.echo("Aborted — typed confirmation did not match.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    async def _run() -> None:
        engine = get_async_engine(
            settings.db_path_for(settings.gekko_user_id),
            get_passphrase(),
        )
        try:
            async with make_session_factory(engine)() as session:
                streak = await compute_clean_streak(
                    session=session,
                    user_id=settings.gekko_user_id,
                    strategy_name=name,
                    account_mode=account_mode,
                )
        finally:
            await engine.dispose()
        if not streak.eligible:
            typer.echo(
                f"Not eligible: {streak.block_reason} "
                f"({streak.clean_count}/{streak.threshold} clean approvals)."
            )
            raise typer.Exit(code=1)
        await promote_strategy_to_auto(
            user_id=settings.gekko_user_id,
            strategy_name=name,
            account_mode=account_mode,
            clean_count=streak.clean_count,
        )

    asyncio.run(_run())
    typer.echo(
        f"Strategy {name!r} is now auto-within-caps ({account_mode})."
    )


@strategy_app.command("demote-auto")
def strategy_demote_auto(
    name: str = typer.Argument(
        ..., help="Strategy slug to demote from auto-execute."
    ),
) -> None:
    """Demote a strategy back to propose-only (TRUST-01 / D-T04).

    One-step, always-safe (removes autonomy → back to HITL). Takes effect on
    the next decision cycle.
    """
    from gekko.config import get_settings
    from gekko.logging_config import configure_logging
    from gekko.strategy.trust import demote_strategy_from_auto
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    asyncio.run(
        demote_strategy_from_auto(
            user_id=settings.gekko_user_id,
            strategy_name=name,
            reason="operator",
        )
    )
    typer.echo(
        f"Strategy {name!r} demoted to propose-only — "
        "takes effect on the next decision cycle."
    )


@strategy_app.command("trust-status")
def strategy_trust_status(
    name: str = typer.Argument(..., help="Strategy slug to inspect."),
    mode: str = typer.Option(
        "PAPER", help="Account mode whose streak to report (PAPER/LIVE)."
    ),
) -> None:
    """Print the clean-approval streak + eligibility for a strategy (TRUST-01)."""
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.strategy.streak import compute_clean_streak
    from gekko.strategy.trust import load_trust_level
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()
    account_mode = mode.strip().upper()

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    async def _run() -> tuple[str, object]:
        trust = await load_trust_level(
            user_id=settings.gekko_user_id,
            strategy_name=name,
            account_mode=account_mode,
        )
        engine = get_async_engine(
            settings.db_path_for(settings.gekko_user_id),
            get_passphrase(),
        )
        try:
            async with make_session_factory(engine)() as session:
                streak = await compute_clean_streak(
                    session=session,
                    user_id=settings.gekko_user_id,
                    strategy_name=name,
                    account_mode=account_mode,
                )
        finally:
            await engine.dispose()
        return trust, streak

    trust, streak = asyncio.run(_run())
    typer.echo(f"Strategy:     {name} ({account_mode})")
    typer.echo(f"Trust level:  {trust}")
    typer.echo(
        f"Clean streak: {streak.clean_count}/{streak.threshold}"
    )
    typer.echo(f"Eligible:     {streak.eligible}")
    if streak.block_reason:
        typer.echo(f"Blocked by:   {streak.block_reason}")


@strategy_app.command("scale-capital")
def strategy_scale_capital(
    name: str = typer.Argument(
        ..., help="Strategy slug whose capital ceiling to set."
    ),
    amount: str = typer.Argument(
        ..., help="New capital ceiling in USD (e.g. 2500.00)."
    ),
) -> None:
    """Set a strategy's capital ceiling (TRUST-03 / D-T14, CLI parity).

    A separate rung from autonomy — never touches trust level or the streak
    (D-T17). Raising the ceiling requires a typed-name confirm (parity with the
    dashboard increase modal); lowering applies immediately. Writes a
    ``capital_scaled`` audit event either way.
    """
    from decimal import Decimal, InvalidOperation

    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.models import StrategyMetadata
    from gekko.db.session import make_session_factory
    from gekko.logging_config import configure_logging
    from gekko.strategy.trust import (
        DEFAULT_CAPITAL_CEILING_USD,
        set_capital_ceiling,
    )
    from gekko.vault.passphrase import get_passphrase, prompt_passphrase

    configure_logging()
    settings = get_settings()

    try:
        new_dec = Decimal(amount)
    except (InvalidOperation, ValueError):
        typer.echo("Aborted — amount must be a number like 2500.00.")
        raise typer.Exit(code=1) from None
    if new_dec < 0:
        typer.echo("Aborted — capital ceiling must be non-negative.")
        raise typer.Exit(code=1)

    try:
        get_passphrase()
    except RuntimeError:
        prompt_passphrase()

    async def _current_ceiling() -> Decimal:
        engine = get_async_engine(
            settings.db_path_for(settings.gekko_user_id),
            get_passphrase(),
        )
        try:
            async with make_session_factory(engine)() as session:
                meta = await session.get(
                    StrategyMetadata, (settings.gekko_user_id, name)
                )
        finally:
            await engine.dispose()
        raw = (
            meta.capital_ceiling_usd
            if meta is not None and meta.capital_ceiling_usd is not None
            else DEFAULT_CAPITAL_CEILING_USD
        )
        return Decimal(str(raw))

    old_dec = asyncio.run(_current_ceiling())
    if new_dec > old_dec:
        typer.echo(
            f"Raising {name!r} capital ceiling from ${old_dec} to ${new_dec}. "
            "This lets the strategy deploy more real capital. "
            "Trust level is unchanged."
        )
        ack = typer.prompt(f"Type the strategy name {name!r} to confirm")
        if ack.strip() != name:
            typer.echo("Aborted — typed confirmation did not match.")
            raise typer.Exit(code=1)

    old_str, new_str = asyncio.run(
        set_capital_ceiling(
            user_id=settings.gekko_user_id,
            strategy_name=name,
            new_ceiling_usd=str(new_dec),
        )
    )
    verb = "Raised" if Decimal(new_str) > Decimal(old_str) else "Lowered"
    typer.echo(
        f"{verb} {name!r} capital ceiling: ${old_str} -> ${new_str}. "
        "Trust level + streak unchanged."
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
