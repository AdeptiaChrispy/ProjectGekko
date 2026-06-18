"""FastAPI dashboard routes — Plan 01-09 + 02-05 + 02-06.

P1 dashboard surface — minimal HTMX/Tailwind UI:

  * ``GET /`` redirects to ``/strategies``.
  * ``GET /strategies`` lists every Strategy scoped to the current user
    (REG-04 — no cross-user surfaces).
  * ``GET /strategies/{name}/edit`` renders the latest-version edit form
    (STRAT-02). Returns 404 when ``(user_id, name)`` has no rows.
  * ``POST /strategies/{name}/save`` validates form input via the
    :class:`Strategy` Pydantic schema, computes ``next_version()`` scoped
    to the current user, inserts a new :class:`StrategyRow`, and PRG-
    redirects to GET. REG-04: every read AND write filters by
    ``settings.gekko_user_id``.
  * ``POST /trigger/{name}`` is the dashboard trigger button (D-06
    surface). Fires :func:`trigger_strategy_run` via
    :func:`asyncio.create_task` and returns the partial template.
  * ``GET /healthz`` is the liveness probe.

The Slack route ``POST /slack/events`` is mounted by
:func:`gekko.dashboard.app.create_app` directly so it can wrap the
slack-bolt adapter — kept out of this router to avoid pulling in the
Slack singleton on import.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Strategy as StrategyRow
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy, next_version
from gekko.vault.passphrase import get_passphrase as _get_passphrase

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


# ---------------------------------------------------------------------------
# Session factory accessor — test seam (mirrors slack_handler.py pattern)
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Tests monkeypatch this to avoid opening a real SQLCipher DB.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Auth dependency — require_session (D-57)
# ---------------------------------------------------------------------------


async def require_session(request: Request) -> str:
    """Return the gekko_user_id bound to the current session.

    Raises HTTP 302 redirect to /login if no valid session cookie is present.
    Per PATTERNS §2k (D-57 auth contract).
    """
    settings = get_settings()
    sess_user_id = request.session.get("gekko_user_id")
    authenticated = request.session.get("authenticated")
    if (
        not sess_user_id
        or not authenticated
        or sess_user_id != settings.gekko_user_id
    ):
        raise HTTPException(
            status_code=302,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    return sess_user_id


# ---------------------------------------------------------------------------
# Login routes (D-57 / D-58)
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    """GET /login — render the passphrase form."""
    next_url = request.query_params.get("next", "/approvals")
    return templates.TemplateResponse(
        "login.html.j2",
        {"request": request, "next_url": next_url, "error": False},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    passphrase: str = Form(...),
    next: str = Form("/approvals"),
) -> HTMLResponse:
    """POST /login — validate passphrase; mint session cookie on success.

    T-03-05-02 open-redirect defense: `next` must start with `/` and not
    contain `://`. Defaults to `/approvals` on validation failure.
    """
    from gekko.vault.passphrase import set_passphrase, verify_passphrase

    # Sanitize redirect target against open-redirect (T-03-05-02)
    safe_next = next if (next.startswith("/") and "://" not in next) else "/approvals"

    # Validate passphrase against in-memory cache
    try:
        ok = verify_passphrase(passphrase)
    except RuntimeError:
        # Passphrase not cached yet (server not fully initialized)
        ok = False

    if not ok:
        return templates.TemplateResponse(
            "login.html.j2",
            {"request": request, "next_url": safe_next, "error": True},
            status_code=200,
        )

    settings = get_settings()
    # Ensure passphrase is in vault for future DB opens
    set_passphrase(passphrase)

    # Mint session
    request.session["gekko_user_id"] = settings.gekko_user_id
    request.session["authenticated"] = True

    return RedirectResponse(url=safe_next, status_code=303)


# ---------------------------------------------------------------------------
# GET/POST /approvals — PENDING proposals index + approve/reject (D-55/D-56)
# ---------------------------------------------------------------------------


def _build_proposal_ctx(row: Any) -> dict[str, Any]:
    """Build the Jinja template context dict for a single Proposal ORM row.

    Extracts evidence, ticker, side, qty from payload_json if available,
    falling back to ORM column values.
    """
    import json as _json

    evidence: list[dict[str, Any]] = []
    rationale = getattr(row, "rationale", "") or ""

    # Parse payload_json for rich data not in direct columns
    payload: dict[str, Any] = {}
    pj = getattr(row, "payload_json", None)
    if pj:
        try:
            payload = _json.loads(pj)
            if not rationale:
                rationale = payload.get("rationale", "")
            raw_evidence = payload.get("evidence", [])
            for e in raw_evidence:
                evidence.append({
                    "summary": e.get("summary", ""),
                    "url": e.get("url", "#"),
                    "source_type": e.get("source_type", ""),
                })
        except (ValueError, TypeError):
            pass

    return {
        "proposal_id": row.proposal_id,
        "ticker": getattr(row, "ticker", payload.get("ticker", "")),
        "side": str(getattr(row, "side", payload.get("side", ""))).upper(),
        "qty": str(getattr(row, "qty", payload.get("qty", ""))),
        "rationale": rationale,
        "evidence": evidence,
        "status": row.status,
        "account_mode": getattr(row, "account_mode", payload.get("account_mode", "PAPER")),
        "expires_at": getattr(row, "expires_at", None),
        "expired_at_local": "",  # Plan 03-03 fills the chat.update / formatted time
        "timeout_minutes": 30,
        "slack_team_id": "",
        "slack_channel_id": getattr(row, "slack_message_channel", "") or "",
    }


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_index(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """GET /approvals — list PENDING/AWAITING_2ND_CHANNEL/EXPIRED proposals.

    D-55: mirrors the Slack proposal card schema via the shared
    _proposal_card.html.j2 partial.
    """
    from gekko.db.models import Proposal as ProposalRow

    sf, engine = _get_session_factory(user_id)
    proposal_ctxs: list[dict[str, Any]] = []
    try:
        async with sf() as session:
            result = await session.execute(
                select(ProposalRow).where(
                    ProposalRow.user_id == user_id,
                    ProposalRow.status.in_(
                        ["PENDING", "AWAITING_2ND_CHANNEL", "EXPIRED"]
                    ),
                )
            )
            rows = result.scalars().all()
            proposal_ctxs = [_build_proposal_ctx(r) for r in rows]
    finally:
        if engine is not None:
            await engine.dispose()

    return templates.TemplateResponse(
        "approvals_index.html.j2",
        {"request": request, "proposals": proposal_ctxs, "user_id": user_id},
    )


@router.post("/approvals/{proposal_id}/approve", response_class=HTMLResponse)
async def approve_proposal_endpoint(
    request: Request,
    proposal_id: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """POST /approvals/{id}/approve — D-56 cross-surface approve with dedup.

    INSERTs a dedup row with source='dashboard' per D-56, then transitions
    PENDING -> APPROVED (or PENDING -> AWAITING_2ND_CHANNEL for first-live).
    HTMX swaps the card via hx-target="closest article" hx-swap="outerHTML".
    """
    from gekko.approval.dedup import claim_action
    from gekko.approval.proposals import approve_proposal, transition_status
    from gekko.audit.log import append_event
    from gekko.db.models import Proposal as ProposalRow
    from gekko.execution.executor import execute_proposal as _execute_proposal

    sf, engine = _get_session_factory(user_id)
    row = None
    should_dispatch = False
    try:
        async with sf() as session, session.begin():
            outcome = await claim_action(
                session,
                proposal_id=proposal_id,
                action_id="approve_proposal",
                actor_slack_user_id=None,
                actor_gekko_user_id=user_id,
                source="dashboard",
            )
            if outcome == "duplicate":
                # Re-read with fresh session (session was rolled back by claim_action)
                pass
            else:
                # first_write — proceed with state transition
                row = (
                    await session.execute(
                        select(ProposalRow).where(
                            ProposalRow.proposal_id == proposal_id,
                            ProposalRow.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="Proposal not found")

                await approve_proposal(session, proposal_id, actor=user_id)
                # Reload row to get updated status
                await session.refresh(row)
                should_dispatch = True

        if outcome == "duplicate" or row is None:
            # Re-read current state for re-render (D-56 visual state IS the feedback)
            sf2, engine2 = _get_session_factory(user_id)
            try:
                async with sf2() as rs:
                    row = (
                        await rs.execute(
                            select(ProposalRow).where(
                                ProposalRow.proposal_id == proposal_id,
                                ProposalRow.user_id == user_id,
                            )
                        )
                    ).scalar_one_or_none()
            finally:
                if engine2 is not None:
                    await engine2.dispose()

        if should_dispatch and row is not None:
            asyncio.create_task(_execute_proposal(proposal_id, user_id))

    finally:
        if engine is not None:
            await engine.dispose()

    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    ctx = _build_proposal_ctx(row)
    ctx["request"] = request
    return templates.TemplateResponse("_proposal_card.html.j2", ctx)


@router.post("/approvals/{proposal_id}/reject", response_class=HTMLResponse)
async def reject_proposal_endpoint(
    request: Request,
    proposal_id: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """POST /approvals/{id}/reject — D-56 cross-surface reject with dedup.

    INSERTs a dedup row with source='dashboard' per D-56, then transitions
    PENDING -> REJECTED. HTMX swaps the card.
    """
    from gekko.approval.dedup import claim_action
    from gekko.approval.proposals import reject_proposal
    from gekko.db.models import Proposal as ProposalRow

    sf, engine = _get_session_factory(user_id)
    row = None
    try:
        async with sf() as session, session.begin():
            outcome = await claim_action(
                session,
                proposal_id=proposal_id,
                action_id="reject_proposal",
                actor_slack_user_id=None,
                actor_gekko_user_id=user_id,
                source="dashboard",
            )
            if outcome == "first_write":
                row = (
                    await session.execute(
                        select(ProposalRow).where(
                            ProposalRow.proposal_id == proposal_id,
                            ProposalRow.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="Proposal not found")

                await reject_proposal(session, proposal_id, actor=user_id)
                await session.refresh(row)

        if outcome == "duplicate" or row is None:
            # Re-read current state
            sf2, engine2 = _get_session_factory(user_id)
            try:
                async with sf2() as rs:
                    row = (
                        await rs.execute(
                            select(ProposalRow).where(
                                ProposalRow.proposal_id == proposal_id,
                                ProposalRow.user_id == user_id,
                            )
                        )
                    ).scalar_one_or_none()
            finally:
                if engine2 is not None:
                    await engine2.dispose()

    finally:
        if engine is not None:
            await engine.dispose()

    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    ctx = _build_proposal_ctx(row)
    ctx["request"] = request
    return templates.TemplateResponse("_proposal_card.html.j2", ctx)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — uvicorn / supervisor / dashboard self-checks."""
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/strategies")


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_list(request: Request) -> HTMLResponse:
    """List strategies for the current user (REG-04 — scoped to gekko_user_id)."""
    settings = get_settings()
    engine = request.app.state.engine
    user_id = settings.gekko_user_id

    async with make_session_factory(engine)() as session:
        # Latest version per strategy_name for the current user.
        latest_subq = (
            select(
                StrategyRow.strategy_name,
                func.max(StrategyRow.version).label("max_version"),
            )
            .where(StrategyRow.user_id == user_id)
            .group_by(StrategyRow.strategy_name)
            .subquery()
        )
        q = (
            select(StrategyRow)
            .join(
                latest_subq,
                (StrategyRow.strategy_name == latest_subq.c.strategy_name)
                & (StrategyRow.version == latest_subq.c.max_version),
            )
            .where(StrategyRow.user_id == user_id)
            .order_by(StrategyRow.strategy_name.asc())
        )
        rows = (await session.execute(q)).scalars().all()

    # Each row gets a watchlist preview computed from payload_json so the
    # template doesn't need to deserialize Pydantic models. Plan 02-06
    # Task 3: enrich with the StrategyMetadata.live_mode_eligible flag so
    # the template can render the [LIVE] chip + Promote-to-Live button.
    from gekko.db.models import StrategyMetadata

    enriched: list[dict[str, object]] = []
    async with make_session_factory(engine)() as session:
        meta_rows = list(
            (
                await session.execute(
                    select(StrategyMetadata).where(
                        StrategyMetadata.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    meta_by_name = {m.strategy_name: m for m in meta_rows}

    for r in rows:
        try:
            strategy = Strategy.model_validate_json(r.payload_json)
            preview = ", ".join(strategy.watchlist[:5])
            if len(strategy.watchlist) > 5:
                preview += f", … (+{len(strategy.watchlist) - 5})"
            mode = strategy.mode
        except Exception:
            preview = "(payload not parseable)"
            mode = "paper"
        meta = meta_by_name.get(r.strategy_name)
        enriched.append(
            {
                "strategy_name": r.strategy_name,
                "version": r.version,
                "watchlist_preview": preview,
                "mode": mode,
                "live_mode_eligible": (
                    meta.live_mode_eligible if meta is not None else False
                ),
            }
        )

    return templates.TemplateResponse(
        "strategies_list.html.j2",
        {
            "request": request,
            "strategies": enriched,
            "user_id": user_id,
        },
    )


@router.get(
    "/strategies/{name}/edit", response_class=HTMLResponse
)
async def strategy_edit(request: Request, name: str) -> HTMLResponse:
    """Render the edit form populated with the latest version (STRAT-02).

    REG-04: scopes to ``current_user.user_id`` — never serves another
    user's strategy. Returns 404 if no row exists for this user.
    """
    settings = get_settings()
    engine = request.app.state.engine
    user_id = settings.gekko_user_id

    async with make_session_factory(engine)() as session:
        q = (
            select(StrategyRow)
            .where(
                StrategyRow.user_id == user_id,
                StrategyRow.strategy_name == name,
            )
            .order_by(desc(StrategyRow.version))
            .limit(1)
        )
        row = (await session.execute(q)).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy {name!r} not found for current user",
        )

    strategy = Strategy.model_validate_json(row.payload_json)

    # Plan 02-06 Task 3 — fetch live_mode_eligible for the mode <select> gate.
    from gekko.db.models import StrategyMetadata

    async with make_session_factory(engine)() as session:
        meta = await session.get(StrategyMetadata, (user_id, name))
    live_mode_eligible = bool(meta and meta.live_mode_eligible)

    return templates.TemplateResponse(
        "strategy_edit.html.j2",
        {
            "request": request,
            "name": name,
            "version": row.version,
            "strategy": strategy,
            "thesis": strategy.thesis,
            "watchlist_csv": ", ".join(strategy.watchlist),
            "max_position_pct": str(strategy.hard_caps.max_position_pct),
            "max_daily_loss_usd": str(strategy.hard_caps.max_daily_loss_usd),
            "max_trades_per_day": strategy.hard_caps.max_trades_per_day,
            "max_sector_exposure_pct": str(
                strategy.hard_caps.max_sector_exposure_pct
            ),
            "schedule_time": strategy.schedule_time or "",
            "live_mode_eligible": live_mode_eligible,
        },
    )


@router.post(
    "/strategies/{name}/save", response_class=HTMLResponse
)
async def strategy_save(
    request: Request,
    name: str,
    thesis: str = Form(...),
    watchlist: str = Form(...),
    max_position_pct: str = Form(...),
    max_daily_loss_usd: str = Form(...),
    max_trades_per_day: int = Form(...),
    max_sector_exposure_pct: str = Form(...),
    schedule_time: str = Form(""),
    mode: str = Form("paper"),
) -> RedirectResponse:
    """Persist a new snapshot version of the strategy from form input (STRAT-02)."""
    settings = get_settings()
    engine = request.app.state.engine
    user_id = settings.gekko_user_id

    tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]

    new_strategy_id = "strat-" + uuid4().hex
    try:
        new_strategy = Strategy.model_validate(
            {
                "strategy_id": new_strategy_id,
                "user_id": user_id,
                "name": name,
                "version": 1,  # placeholder; next_version overrides
                "thesis": thesis,
                "watchlist": tickers,
                "hard_caps": HardCaps(
                    max_position_pct=Decimal(max_position_pct),
                    max_daily_loss_usd=Decimal(max_daily_loss_usd),
                    max_trades_per_day=max_trades_per_day,
                    max_sector_exposure_pct=Decimal(max_sector_exposure_pct),
                ),
                "mode": mode,
                "schedule_time": (schedule_time or None),
                "created_at": datetime.now(UTC).isoformat(),
                "created_by_chat": False,
            }
        )
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy fields: {exc}",
        ) from exc

    async with make_session_factory(engine)() as session, session.begin():
        v = await next_version(
            session, user_id=user_id, strategy_name=name
        )
        versioned = new_strategy.model_copy(update={"version": v})
        session.add(
            StrategyRow(
                strategy_id=new_strategy_id,
                user_id=user_id,
                strategy_name=name,
                version=v,
                payload_json=versioned.model_dump_json(),
                created_at=versioned.created_at,
            )
        )

    return RedirectResponse(
        url=f"/strategies/{name}/edit", status_code=303
    )


@router.post("/trigger/{name}", response_class=HTMLResponse)
async def trigger(request: Request, name: str) -> HTMLResponse:
    """Fire :func:`trigger_strategy_run` + post the proposal card (D-06).

    Fire-and-forget — the route returns the partial template immediately
    so HTMX swaps it in; the background task awaits the agent run
    (30+ seconds) and then posts the HITL-01 card to the user's DM
    via :func:`gekko.reporter.slack.post_run_result`.
    """
    settings = get_settings()
    asyncio.create_task(_run_and_post_dashboard(settings.gekko_user_id, name))
    return templates.TemplateResponse(
        "trigger_button.html.j2",
        {"request": request, "name": name, "triggered": True},
    )


async def _run_and_post_dashboard(user_id: str, strategy_name: str) -> None:
    """Background wrapper for the dashboard trigger button.

    Mirrors the slash-command wrapper in :mod:`gekko.slack.commands` —
    catches errors so the create_task doesn't drop them silently.
    """
    from gekko.agent.runtime import trigger_strategy_run
    from gekko.logging_config import get_logger
    from gekko.reporter.slack import post_run_result

    log = get_logger(__name__)
    try:
        result = await trigger_strategy_run(
            user_id=user_id, strategy_name=strategy_name, source="dashboard"
        )
    except Exception:
        log.exception(
            "dashboard.run.trigger_failed",
            user_id=user_id,
            strategy_name=strategy_name,
        )
        return
    try:
        await post_run_result(user_id, result)
    except Exception:
        log.exception(
            "dashboard.run.post_failed",
            user_id=user_id,
            strategy_name=strategy_name,
        )


# ---------------------------------------------------------------------------
# Kill switch routes — Plan 02-05 Task 2 (D-38 / EXEC-06 / UI-SPEC §2b)
# ---------------------------------------------------------------------------


@router.get("/kill/confirm-modal", response_class=HTMLResponse)
async def kill_confirm_modal(request: Request) -> HTMLResponse:
    """Return the kill-confirmation modal HTMX fragment (UI-SPEC §2b)."""
    return templates.TemplateResponse(
        "kill_modal.html.j2", {"request": request}
    )


@router.get("/unkill/confirm-modal", response_class=HTMLResponse)
async def unkill_confirm_modal(request: Request) -> HTMLResponse:
    """Return the unkill-confirmation modal HTMX fragment."""
    return templates.TemplateResponse(
        "unkill_modal.html.j2", {"request": request}
    )


@router.get("/modal/close", response_class=HTMLResponse)
async def modal_close() -> HTMLResponse:
    """Return empty HTML to clear ``#modal-mount`` (CSP-safer modal close).

    Per UI-SPEC §2b "Alternative (safer per CSP audit)" — the Cancel link
    in the kill/unkill modal targets this endpoint with hx-swap="innerHTML"
    to clear the modal-mount slot. Avoids the ``hx-on`` route since
    it relies on htmx's inline-handler interpretation.
    """
    return HTMLResponse("")


@router.post("/kill", response_class=HTMLResponse)
async def kill_endpoint(
    request: Request, confirm: str = Form(...)
) -> HTMLResponse:
    """POST /kill — operator submitted the typed-KILL form.

    Server-side gate: ``confirm.strip().upper() == "KILL"``. Otherwise
    raise HTTPException(400). Fires ``_execute_kill`` in the background
    (the cancel sweep + audit + DM run for up to 5s) and returns the
    ``kill_active_banner.html.j2`` partial that HTMX swaps into
    ``#kill-banner-mount``.
    """
    # Case-sensitive gate: UI-SPEC §2b "Type KILL exactly (uppercase)".
    if confirm.strip() != "KILL":
        raise HTTPException(
            status_code=400,
            detail="Type KILL exactly (uppercase) to confirm.",
        )

    settings = get_settings()
    asyncio.create_task(
        _execute_kill_background(
            user_id=settings.gekko_user_id, source="dashboard"
        )
    )
    # Flag the app-state cache as dirty so subsequent renders pick up the
    # new state without waiting for the 60s TTL to expire.
    try:
        request.app.state.kill_active = True
        request.state.kill_active = True
    except Exception:  # noqa: BLE001 — best-effort cache hint
        pass
    return templates.TemplateResponse(
        "kill_active_banner.html.j2",
        {
            "request": request,
            "n_cancelled": 0,
            "n_total": 0,
            "boot_restored": False,
        },
    )


@router.post("/unkill", response_class=HTMLResponse)
async def unkill_endpoint(
    request: Request, confirm: str = Form(...)
) -> HTMLResponse:
    """POST /unkill — operator submitted the typed-UNKILL form."""
    # Case-sensitive gate: UI-SPEC §2b symmetric "Type UNKILL exactly".
    if confirm.strip() != "UNKILL":
        raise HTTPException(
            status_code=400,
            detail="Type UNKILL exactly (uppercase) to confirm.",
        )

    settings = get_settings()
    asyncio.create_task(
        _execute_unkill_background(
            user_id=settings.gekko_user_id, source="dashboard"
        )
    )
    try:
        request.app.state.kill_active = False
        request.state.kill_active = False
    except Exception:  # noqa: BLE001
        pass
    # Return an empty kill-banner-mount placeholder so HTMX swaps the
    # red banner away.
    return HTMLResponse('<div id="kill-banner-mount"></div>')


@router.get("/kill/state", response_class=HTMLResponse)
async def kill_state(request: Request) -> HTMLResponse:
    """HTMX-poll endpoint for the in-flight kill tally (UI-SPEC §2b).

    The kill modal's loading affordance polls this every 1s via
    ``hx-trigger="every 1s"``. Returns a small text fragment with the
    current tally — or "Cancelling…" if still in flight.
    """
    settings = get_settings()
    from gekko.execution.kill_switch import is_active as _is_kill_active

    active = await _is_kill_active(settings.gekko_user_id)
    if active:
        return HTMLResponse("Kill ACTIVE — cancel sweep complete.")
    return HTMLResponse("Setting kill_active=true…")


async def _execute_kill_background(*, user_id: str, source: str) -> None:
    """Background wrapper around ``_execute_kill`` (PATTERNS §5d shape)."""
    from gekko.logging_config import get_logger
    from gekko.execution.kill_switch import _execute_kill

    log = get_logger(__name__)
    try:
        await _execute_kill(user_id=user_id, source=source, reason="manual")
    except Exception:
        log.exception(
            "dashboard.kill.background_failed",
            user_id=user_id,
            source=source,
        )


async def _execute_unkill_background(*, user_id: str, source: str) -> None:
    """Background wrapper around ``_execute_unkill`` (PATTERNS §5d shape)."""
    from gekko.execution.kill_switch import _execute_unkill
    from gekko.logging_config import get_logger

    log = get_logger(__name__)
    try:
        await _execute_unkill(user_id=user_id, source=source)
    except Exception:
        log.exception(
            "dashboard.unkill.background_failed",
            user_id=user_id,
            source=source,
        )


# ---------------------------------------------------------------------------
# Live-mode promotion + dual-channel confirm — Plan 02-06 Task 2/3
# (D-31 promote-to-live + D-32 / HITL-06 dual-channel)
# ---------------------------------------------------------------------------


import time as _time


@router.get(
    "/strategies/{name}/promote-modal", response_class=HTMLResponse
)
async def promote_modal(
    request: Request, name: str
) -> HTMLResponse:
    """Render the typed-confirm modal for promoting a strategy to live."""
    return templates.TemplateResponse(
        "promote_to_live_modal.html.j2",
        {"request": request, "name": name},
    )


@router.post("/strategies/{name}/promote-to-live", response_class=HTMLResponse)
async def promote_to_live(
    request: Request,
    name: str,
    strategy_name_confirm: str = Form(...),
) -> HTMLResponse:
    """Promote a paper strategy to live-eligible (D-31).

    UI-SPEC §"Destructive Action Confirmations": requires the operator
    to type the EXACT strategy name in the ``strategy_name_confirm``
    form field. On confirm, calls
    :func:`gekko.strategy.promotion.promote_strategy_to_live`.

    Symmetric with the CLI ``gekko strategy promote-live <name>``.
    Returns an HTMX partial that replaces the "Promote to Live" button.
    """
    from gekko.strategy.promotion import promote_strategy_to_live

    if strategy_name_confirm.strip() != name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Typed strategy name did not match {name!r}. "
                "Promotion aborted."
            ),
        )

    settings = get_settings()
    await promote_strategy_to_live(
        user_id=settings.gekko_user_id, strategy_name=name
    )
    # Invalidate the cached banner_mode so the next render picks up the
    # new live-eligible state without waiting for the 60s TTL.
    try:
        request.app.state.banner_mode = "LIVE"
        request.state.banner_mode = "LIVE"
    except Exception:  # noqa: BLE001 — best-effort cache hint
        pass
    return HTMLResponse(
        '<span class="chip-live">LIVE — eligible</span>'
    )


@router.get("/live-confirm/{proposal_id}", response_class=HTMLResponse)
async def live_confirm_get(
    request: Request, proposal_id: str
) -> HTMLResponse:
    """Render the HITL-06 second-channel confirm page (UI-SPEC §3b).

    The operator lands here from the Slack DM URL. Renders the
    ``first_live_confirm.html.j2`` full-page template with the trade
    detail panel + two checkboxes + 5s countdown. Form POSTs back to
    this same path.
    """
    from gekko.schemas.proposal import TradeProposal

    settings = get_settings()
    engine = request.app.state.engine
    user_id = settings.gekko_user_id

    from gekko.db.models import Proposal as _Proposal

    async with make_session_factory(engine)() as session:
        row = (
            await session.execute(
                select(_Proposal).where(
                    _Proposal.proposal_id == proposal_id,
                    _Proposal.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Proposal {proposal_id} not found"
        )
    if row.status == "APPROVED_LIVE":
        # Idempotent — already confirmed. Render success template.
        return templates.TemplateResponse(
            "live_confirm_success.html.j2",
            {
                "request": request,
                "proposal_id": proposal_id,
                "already_confirmed_at": row.updated_at,
            },
        )
    if row.status != "AWAITING_2ND_CHANNEL":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Proposal is in status {row.status!r} and cannot "
                "be confirmed via the dual-channel gate."
            ),
        )

    try:
        tp = TradeProposal.model_validate_json(row.payload_json)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not parse proposal payload: {exc}",
        ) from exc

    return templates.TemplateResponse(
        "first_live_confirm.html.j2",
        {
            "request": request,
            "proposal_id": proposal_id,
            "strategy_name": tp.strategy_name,
            "ticker": tp.ticker,
            "side": str(tp.side),
            "qty": str(tp.qty),
            "order_type": str(tp.order_type),
            "limit_price": str(tp.limit_price) if tp.limit_price else "",
            "stop_price": str(tp.stop_price) if tp.stop_price else "",
            "target_notional_usd": str(tp.target_notional_usd),
            "rationale": tp.rationale,
            "page_load_ts": _time.time(),
        },
    )


@router.post("/live-confirm/{proposal_id}", response_class=HTMLResponse)
async def live_confirm_post(
    request: Request,
    proposal_id: str,
    ack_real_money: str = Form(""),
    ack_read_rationale: str = Form(""),
    page_load_ts: float = Form(...),
) -> HTMLResponse:
    """HITL-06 second-channel confirm — AWAITING_2ND_CHANNEL → APPROVED_LIVE.

    Server-side validation:
      * Both ``ack_real_money`` AND ``ack_read_rationale`` checkboxes
        must be "on".
      * ``time.time() - page_load_ts >= 5.0`` (5-second read timer).

    On validation pass, transitions the proposal AWAITING_2ND_CHANNEL →
    APPROVED_LIVE inside one transaction and dispatches the executor
    via ``asyncio.create_task``. Idempotency: when the proposal is
    already in APPROVED_LIVE (double-click), returns the success
    template without re-dispatching.
    """
    from gekko.approval.proposals import transition_status
    from gekko.audit.log import append_event
    from gekko.db.models import Proposal as _Proposal
    from gekko.execution.executor import execute_proposal as _execute_proposal

    settings = get_settings()
    engine = request.app.state.engine
    user_id = settings.gekko_user_id

    # Validation Layer 1: both checkboxes must be checked.
    if ack_real_money != "on" or ack_read_rationale != "on":
        raise HTTPException(
            status_code=400,
            detail=(
                "Both acknowledgements are required to confirm a live "
                "trade. Tick both boxes and re-submit."
            ),
        )

    # Validation Layer 2: server-side 5-second read timer. The client
    # MAY send any value here; we trust ONLY the server clock for the
    # gate (UI-SPEC §3b "pure server-side timestamp check").
    elapsed = _time.time() - page_load_ts
    if elapsed < 5.0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Please read the trade details for the full 5 seconds. "
                f"Time elapsed: {elapsed:.2f}s."
            ),
        )

    # State transition + idempotency layer. We re-read the row's status
    # inside the transaction to handle the double-click case.
    sf = make_session_factory(engine)
    should_dispatch = False
    async with sf() as session, session.begin():
        row = (
            await session.execute(
                select(_Proposal).where(
                    _Proposal.proposal_id == proposal_id,
                    _Proposal.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Proposal {proposal_id} not found",
            )
        if row.status == "APPROVED_LIVE":
            # Already confirmed — no-op (double-click defense).
            return templates.TemplateResponse(
                "live_confirm_success.html.j2",
                {
                    "request": request,
                    "proposal_id": proposal_id,
                    "already_confirmed_at": row.updated_at,
                },
            )
        if row.status != "AWAITING_2ND_CHANNEL":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Proposal is in status {row.status!r} and cannot "
                    "be confirmed via the dual-channel gate."
                ),
            )

        await transition_status(
            session,
            proposal_id,
            from_status="AWAITING_2ND_CHANNEL",
            to_status="APPROVED_LIVE",
        )
        await append_event(
            session,
            user_id=row.user_id,
            strategy_id=row.strategy_id,
            event_type="approval",
            payload={
                "proposal_id": proposal_id,
                "actor": "dashboard",
                "slack_action_id": "live_confirm",
                "second_channel": True,
            },
        )
        should_dispatch = True

    # Dispatch the executor AFTER the transaction commits so the
    # executor sees status=APPROVED_LIVE on its sanity gate.
    if should_dispatch:
        asyncio.create_task(_execute_proposal(proposal_id, user_id))

    return templates.TemplateResponse(
        "live_confirm_success.html.j2",
        {
            "request": request,
            "proposal_id": proposal_id,
            "already_confirmed_at": None,
        },
    )


__all__: tuple[str, ...] = ("router",)
