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
from urllib.parse import urlparse
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
# Must be defined BEFORE the router so the APIRouter(dependencies=[...])
# constructor can reference the callable at module-load time.
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
# Routers — fail-closed design:
#
# ``router`` has ``Depends(require_session)`` as a router-level dependency so
# every route it hosts is auth-gated by default. Public routes that must be
# exempt from authentication (/login GET/POST, /healthz) are declared on
# ``public_router`` (no router-level dependency). Both routers are registered
# in ``app.py`` via ``app.include_router()``.
#
# NOTE: FastAPI's ``dependencies=[]`` on a per-route decorator does NOT
# override router-level dependencies — it is additive. The two-router
# pattern is the correct approach for per-route public exemptions.
# ---------------------------------------------------------------------------

public_router = APIRouter()  # no auth dependency — for /login and /healthz
router = APIRouter(dependencies=[Depends(require_session)])


# ---------------------------------------------------------------------------
# Login routes (D-57 / D-58) — declared on public_router (no auth gate)
# ---------------------------------------------------------------------------


@public_router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    """GET /login — render the passphrase form."""
    next_url = request.query_params.get("next", "/approvals")
    return templates.TemplateResponse(
        "login.html.j2",
        {"request": request, "next_url": next_url, "error": False},
    )


@public_router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    passphrase: str = Form(...),
    next: str = Form("/approvals"),
) -> HTMLResponse:
    """POST /login — validate passphrase; mint session cookie on success.

    T-03-05-02 open-redirect defense: `next` must be a same-origin absolute
    path (no scheme, no netloc, single leading `/`). Defaults to `/approvals`
    on validation failure.
    """
    from gekko.vault.passphrase import set_passphrase, verify_passphrase

    # Sanitize redirect target against open-redirect (T-03-05-02).
    # Require a same-origin absolute path: no scheme, no netloc, and a path
    # starting with a single "/" — this rejects protocol-relative ("//evil.com")
    # and backslash ("/\evil.com") variants that a bare startswith("/") allows.
    _parsed = urlparse(next)
    safe_next = (
        next
        if (
            not _parsed.scheme
            and not _parsed.netloc
            and next.startswith("/")
            and not next.startswith(("//", "/\\"))
        )
        else "/approvals"
    )

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

    # Compact-card fields (legibility): dollar cost + one-line summary so the
    # operator sees "what + how much + why (briefly)" without a wall of text.
    cost_raw = payload.get("target_notional_usd")
    try:
        cost = f"${Decimal(str(cost_raw)):,.2f}" if cost_raw not in (None, "") else ""
    except (InvalidOperation, ValueError, TypeError):
        cost = ""
    summary = rationale.strip().replace("\n", " ")
    if len(summary) > 140:
        summary = summary[:139].rstrip() + "…"

    return {
        "proposal_id": row.proposal_id,
        "ticker": getattr(row, "ticker", payload.get("ticker", "")),
        "side": str(getattr(row, "side", payload.get("side", ""))).upper(),
        "qty": str(getattr(row, "qty", payload.get("qty", ""))),
        "cost": cost,
        "summary": summary,
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


@router.get("/approvals/poll", response_class=HTMLResponse)
async def approvals_poll(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """GET /approvals/poll — HTMX polling partial for the proposal list (Plan 03-13).

    Returns the _proposals_list.html.j2 fragment (no full page wrapper) so
    HTMX can replace the proposal-list container every 30 seconds without a
    full page reload.

    Auth: on the authenticated router — require_session fires automatically.
    Unauthenticated poll returns 302 → /login (session expiry safe).

    T-03-13-01: poll route inherits require_session from the authenticated
    router; unauthenticated → 302 /login (STRIDE Spoofing mitigated).
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
        "_proposals_list.html.j2",
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


# ---------------------------------------------------------------------------
# Edit-size modal — GET (render form) + POST (drift check + transition)
# ---------------------------------------------------------------------------


@router.get("/approvals/{proposal_id}/edit-size", response_class=HTMLResponse)
async def edit_size_get(
    request: Request,
    proposal_id: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """GET /approvals/{id}/edit-size — render HTMX edit-size modal partial.

    Loads the Proposal row to pre-fill qty, ref_price, target_notional_usd.
    Returns edit_size_modal.html.j2 which HTMX swaps into #modal-mount.
    """
    from decimal import Decimal as _Decimal
    from gekko.db.models import Proposal as ProposalRow

    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == proposal_id,
                        ProposalRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
    finally:
        if engine is not None:
            await engine.dispose()

    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    import json as _json
    payload = _json.loads(row.payload_json)
    ticker = payload.get("ticker", row.ticker or "")
    qty = payload.get("qty", "0")
    limit_price = payload.get("limit_price")
    stop_price = payload.get("stop_price")
    target_notional_usd = payload.get("target_notional_usd", "0")

    # Derive ref_price: limit > stop > target/qty fallback
    if limit_price:
        ref_price = str(limit_price)
    elif stop_price:
        ref_price = str(stop_price)
    else:
        try:
            ref_price = str(_Decimal(target_notional_usd) / _Decimal(qty))
        except Exception:
            ref_price = "0"

    side = payload.get("side", "")

    # Compute original_notional for plain-language framing
    try:
        original_notional = str(_Decimal(qty) * _Decimal(ref_price))
    except Exception:
        original_notional = "0"

    return templates.TemplateResponse(
        request,
        "edit_size_modal.html.j2",
        {
            "proposal_id": proposal_id,
            "ticker": ticker,
            "qty": qty,
            "side": side,
            "ref_price": ref_price,
            "target_notional_usd": target_notional_usd,
            "original_notional": original_notional,
            "drift_error": None,
        },
    )


@router.post("/approvals/{proposal_id}/edit-submit", response_class=HTMLResponse)
async def edit_size_submit(
    request: Request,
    proposal_id: str,
    qty: str = Form(...),
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """POST /approvals/{id}/edit-submit — D-54 cap check + dedup + transition (Plan 03-11).

    Runs _check_edit_size_caps (NOT _drift_check) to validate the operator's
    edited qty against the strategy's OrderGuard hard caps. _drift_check is the
    agent's output-consistency guard (D-27) and is NOT applied to operator edits.
    On cap fail: re-render edit_size_modal.html.j2 with plain-language error block.
    On pass: dedup INSERT (source='dashboard') + edit_size event + qty update +
    PENDING -> APPROVED + background execute_proposal. Returns _proposal_card.html.j2
    with APPROVED state.
    """
    from decimal import Decimal as _Decimal, InvalidOperation as _InvalidOp
    import json as _json
    from gekko.approval.actions import _check_edit_size_caps
    from gekko.approval.dedup import claim_action
    from gekko.approval.proposals import append_event as _append_event, transition_status
    from gekko.audit.canonical import normalize_decimals
    from gekko.db.models import Proposal as ProposalRow
    from gekko.execution.executor import execute_proposal as _execute_proposal
    from gekko.schemas.proposal import TradeProposal

    # 1. Parse and validate qty input
    try:
        new_qty = _Decimal(qty.strip())
        if new_qty <= 0:
            raise _InvalidOp("qty must be positive")
    except (_InvalidOp, Exception):
        # Re-render modal with input error
        return templates.TemplateResponse(
            request,
            "edit_size_modal.html.j2",
            {
                "proposal_id": proposal_id,
                "ticker": "",
                "qty": qty,
                "side": "",
                "ref_price": "0",
                "target_notional_usd": "0",
                "original_notional": "0",
                "drift_error": "Please enter a valid positive quantity.",
            },
        )

    # 2. Load proposal to get ref_price + strategy for cap check
    sf, engine = _get_session_factory(user_id)
    row = None
    try:
        async with sf() as session:
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
            # Keep a snapshot for processing outside the session
            payload_json_snapshot = row.payload_json
            strategy_id_snapshot = row.strategy_id
    finally:
        if engine is not None:
            await engine.dispose()

    payload = _json.loads(payload_json_snapshot)
    ticker = payload.get("ticker", "")
    side = payload.get("side", "")
    original_qty_str = payload.get("qty", "0")
    limit_price = payload.get("limit_price")
    stop_price = payload.get("stop_price")
    target_notional_usd_str = payload.get("target_notional_usd", "0")
    # CR-01 fix: read account_mode from the proposal payload so the cap gate
    # can be mode-aware. Default to "LIVE" (fail-closed = safest) if missing.
    _account_mode: str = payload.get("account_mode", "LIVE") or "LIVE"

    try:
        target_notional = _Decimal(target_notional_usd_str)
        original_qty = _Decimal(original_qty_str)
    except _InvalidOp:
        target_notional = _Decimal("0")
        original_qty = _Decimal("0")

    # Derive ref_price server-side (not from operator input)
    if limit_price:
        ref_price = _Decimal(str(limit_price))
    elif stop_price:
        ref_price = _Decimal(str(stop_price))
    elif original_qty and target_notional:
        ref_price = target_notional / original_qty
    else:
        ref_price = _Decimal("0")

    original_notional_str = str(original_qty * ref_price)

    # 3. Cap check — validate against strategy hard caps, not 2% drift (Plan 03-11)
    #    Fetch account equity from the paper broker; use 0 (fail-open) on any error.
    from gekko.schemas.strategy import Strategy as _Strategy

    equity = _Decimal("0")
    strategy_obj: _Strategy | None = None

    # Load strategy for hard caps
    sf2, engine2 = _get_session_factory(user_id)
    try:
        async with sf2() as session2:
            from gekko.db.models import Strategy as _StrategyRow
            strategy_row = (
                await session2.execute(
                    select(_StrategyRow).where(
                        _StrategyRow.user_id == user_id,
                        _StrategyRow.strategy_id == strategy_id_snapshot,
                    )
                )
            ).scalar_one_or_none()
            if strategy_row is not None and strategy_row.payload_json:
                try:
                    strategy_obj = _Strategy.model_validate_json(
                        strategy_row.payload_json
                    )
                except Exception:  # noqa: BLE001 — corrupt payload → skip cap
                    strategy_obj = None
    finally:
        if engine2 is not None:
            await engine2.dispose()

    # Fetch account equity from paper broker
    try:
        from gekko.brokers.alpaca import AlpacaBroker as _AlpacaBroker
        _settings = get_settings()
        _paper_key = _settings.alpaca_paper_api_key
        _paper_secret = _settings.alpaca_paper_secret_key
        _broker = _AlpacaBroker(
            api_key=_paper_key.get_secret_value(),
            secret_key=_paper_secret.get_secret_value(),
            paper=True,
        )
        import asyncio as _asyncio
        try:
            _account = await _asyncio.wait_for(_broker.get_account(), timeout=2.5)
            _eq_raw = _account.get("equity") or _account.get("portfolio_value") or "0"
            equity = _Decimal(str(_eq_raw))
        except Exception:  # noqa: BLE001 — timeout/broker error → fail-open
            from gekko.logging_config import get_logger as _get_logger
            _log = _get_logger(__name__)
            _log.warning(
                "dashboard.edit_size.equity_fetch_failed",
                proposal_id=proposal_id,
                note="cap check will skip (equity=0 fail-open)",
            )
            equity = _Decimal("0")
    except Exception:  # noqa: BLE001 — broker construction failed → fail-open
        equity = _Decimal("0")

    # CR-01 fix: mode-aware fail-closed when strategy caps cannot be verified.
    # LIVE proposals: reject the edit — real money, no backstop if OrderGuard
    #   also fails at execute time. PAPER: keep fail-open (OrderGuard re-checks
    #   at execute_proposal). Matches Plan 03-11 threat model T-03-11-04.
    if strategy_obj is None:
        if _account_mode == "LIVE":
            from gekko.logging_config import get_logger as _get_logger
            _log2 = _get_logger(__name__)
            _log2.warning(
                "dashboard.edit_size.strategy_load_failed_live_rejected",
                proposal_id=proposal_id,
                account_mode=_account_mode,
            )
            return templates.TemplateResponse(
                request,
                "edit_size_modal.html.j2",
                {
                    "proposal_id": proposal_id,
                    "ticker": ticker,
                    "qty": qty,
                    "side": side,
                    "ref_price": str(ref_price),
                    "target_notional_usd": target_notional_usd_str,
                    "original_notional": original_notional_str,
                    "drift_error": (
                        "Couldn't verify your strategy's risk caps right now"
                        " — edit blocked for safety. Please try again."
                    ),
                },
            )
        else:
            from gekko.logging_config import get_logger as _get_logger
            _log2 = _get_logger(__name__)
            _log2.warning(
                "dashboard.edit_size.strategy_load_failed_paper_allow",
                proposal_id=proposal_id,
                account_mode=_account_mode,
                note="cap check skipped; OrderGuard re-checks at execute_proposal",
            )
    else:
        _ok, _cap_msg = _check_edit_size_caps(new_qty, ref_price, strategy_obj, equity)
        if not _ok:
            return templates.TemplateResponse(
                request,
                "edit_size_modal.html.j2",
                {
                    "proposal_id": proposal_id,
                    "ticker": ticker,
                    "qty": qty,
                    "side": side,
                    "ref_price": str(ref_price),
                    "target_notional_usd": target_notional_usd_str,
                    "original_notional": original_notional_str,
                    "drift_error": _cap_msg,
                },
            )

    # 4. Cap passed — dedup INSERT + audit event + qty update + transition
    # WR-01 fix: initialise before the try/finally so references at the
    # bottom of the function are never unbound if an exception propagates.
    outcome: str = ""
    updated_row = None
    sf3, engine3 = _get_session_factory(user_id)
    try:
        async with sf3() as session2, session2.begin():
            outcome = await claim_action(
                session2,
                proposal_id=proposal_id,
                action_id="edit_size",
                actor_slack_user_id=None,
                actor_gekko_user_id=user_id,
                source="dashboard",
            )
            if outcome == "first_write":
                row2 = (
                    await session2.execute(
                        select(ProposalRow).where(
                            ProposalRow.proposal_id == proposal_id,
                            ProposalRow.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if row2 is None:
                    raise HTTPException(status_code=404, detail="Proposal not found")

                tp = TradeProposal.model_validate_json(row2.payload_json)
                old_qty = tp.qty
                old_notional = old_qty * ref_price
                new_notional = new_qty * ref_price

                from gekko.audit.log import append_event as _ae
                await _ae(
                    session2,
                    user_id=user_id,
                    strategy_id=row2.strategy_id,
                    event_type="edit_size",
                    payload=normalize_decimals({
                        "old_qty": old_qty,
                        "new_qty": new_qty,
                        "old_notional": old_notional,
                        "new_notional": new_notional,
                        "actor": user_id,
                    }),
                )

                # Update payload_json with new qty (PATTERNS §3 re-serialize)
                tp_updated = tp.model_copy(update={"qty": new_qty})
                row2.payload_json = tp_updated.model_dump_json()

                await transition_status(
                    session2,
                    proposal_id,
                    from_status="PENDING",
                    to_status="APPROVED",
                )
                await session2.refresh(row2)
                updated_row = row2
            else:
                # Duplicate — re-read
                updated_row = (
                    await session2.execute(
                        select(ProposalRow).where(
                            ProposalRow.proposal_id == proposal_id,
                            ProposalRow.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
    finally:
        if engine3 is not None:
            await engine3.dispose()

    if outcome == "first_write":
        asyncio.create_task(_execute_proposal(proposal_id, user_id))

    if updated_row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # 5. Return updated proposal card for HTMX swap (closes modal + refreshes card)
    ctx = _build_proposal_ctx(updated_row)
    ctx["request"] = request
    return templates.TemplateResponse("_proposal_card.html.j2", ctx)


# ---------------------------------------------------------------------------
# Settings — GET (render form) + POST (validate + save quiet hours)
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """GET /settings — render quiet hours configuration form (UI-SPEC §Surface 5)."""
    import zoneinfo
    from gekko.db.models import User as UserRow

    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            user = (
                await session.execute(
                    select(UserRow).where(UserRow.user_id == user_id)
                )
            ).scalar_one_or_none()
    finally:
        if engine is not None:
            await engine.dispose()

    # user may be None for early-bootstrap state; provide safe defaults
    iana_timezones = sorted(zoneinfo.available_timezones())
    return templates.TemplateResponse(
        request,
        "settings.html.j2",
        {
            "user": user,
            "iana_timezones": iana_timezones,
            "success": False,
            "error": None,
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(
    request: Request,
    timezone: str = Form(""),
    quiet_hours_start: str = Form(""),
    quiet_hours_end: str = Form(""),
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """POST /settings — validate + save quiet hours (UI-SPEC §Surface 5).

    Validation:
    - timezone must be in zoneinfo.available_timezones()
    - quiet_hours_start and quiet_hours_end must both be blank OR both set
    """
    import zoneinfo
    from gekko.db.models import User as UserRow

    iana_timezones = sorted(zoneinfo.available_timezones())

    # Validation
    error: str | None = None
    if timezone and timezone not in zoneinfo.available_timezones():
        error = f'Timezone "{timezone}" is not a valid IANA timezone.'
    elif bool(quiet_hours_start) != bool(quiet_hours_end):
        error = "Quiet hours start and end must both be set, or both be blank."

    if error:
        sf, engine = _get_session_factory(user_id)
        try:
            async with sf() as session:
                user = (
                    await session.execute(
                        select(UserRow).where(UserRow.user_id == user_id)
                    )
                ).scalar_one_or_none()
        finally:
            if engine is not None:
                await engine.dispose()
        return templates.TemplateResponse(
            request,
            "settings.html.j2",
            {
                "user": user,
                "iana_timezones": iana_timezones,
                "success": False,
                "error": error,
            },
        )

    # Save settings
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            user = (
                await session.execute(
                    select(UserRow).where(UserRow.user_id == user_id)
                )
            ).scalar_one_or_none()
            if user is not None:
                if timezone:
                    user.timezone = timezone
                user.quiet_hours_start = quiet_hours_start or None
                user.quiet_hours_end = quiet_hours_end or None
            await session.flush()
    finally:
        if engine is not None:
            await engine.dispose()

    # Re-read for re-render
    sf2, engine2 = _get_session_factory(user_id)
    try:
        async with sf2() as session2:
            user = (
                await session2.execute(
                    select(UserRow).where(UserRow.user_id == user_id)
                )
            ).scalar_one_or_none()
    finally:
        if engine2 is not None:
            await engine2.dispose()

    return templates.TemplateResponse(
        request,
        "settings.html.j2",
        {
            "user": user,
            "iana_timezones": iana_timezones,
            "success": True,
            "error": None,
        },
    )


@public_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — uvicorn / supervisor / dashboard self-checks."""
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/strategies")


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_list(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """List strategies for the current user (REG-04 — scoped to gekko_user_id)."""
    engine = request.app.state.engine

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
async def strategy_edit(
    request: Request,
    name: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """Render the edit form populated with the latest version (STRAT-02).

    REG-04: scopes to ``current_user.user_id`` — never serves another
    user's strategy. Returns 404 if no row exists for this user.
    """
    engine = request.app.state.engine

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
    user_id: str = Depends(require_session),
) -> RedirectResponse:
    """Persist a new snapshot version of the strategy from form input (STRAT-02)."""
    engine = request.app.state.engine

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
async def trigger(
    request: Request,
    name: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """Fire :func:`trigger_strategy_run` + post the proposal card (D-06).

    Fire-and-forget — the route returns the partial template immediately
    so HTMX swaps it in; the background task awaits the agent run
    (30+ seconds) and then posts the HITL-01 card to the user's DM
    via :func:`gekko.reporter.slack.post_run_result`.
    """
    asyncio.create_task(_run_and_post_dashboard(user_id, name))
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
    request: Request,
    confirm: str = Form(...),
    user_id: str = Depends(require_session),
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

    asyncio.create_task(
        _execute_kill_background(
            user_id=user_id, source="dashboard"
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
    request: Request,
    confirm: str = Form(...),
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """POST /unkill — operator submitted the typed-UNKILL form."""
    # Case-sensitive gate: UI-SPEC §2b symmetric "Type UNKILL exactly".
    if confirm.strip() != "UNKILL":
        raise HTTPException(
            status_code=400,
            detail="Type UNKILL exactly (uppercase) to confirm.",
        )

    asyncio.create_task(
        _execute_unkill_background(
            user_id=user_id, source="dashboard"
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
async def kill_state(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """HTMX-poll endpoint for the in-flight kill tally (UI-SPEC §2b).

    The kill modal's loading affordance polls this every 1s via
    ``hx-trigger="every 1s"``. Returns a small text fragment with the
    current tally — or "Cancelling…" if still in flight.
    """
    from gekko.execution.kill_switch import is_active as _is_kill_active

    active = await _is_kill_active(user_id)
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
    user_id: str = Depends(require_session),
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

    await promote_strategy_to_live(
        user_id=user_id, strategy_name=name
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
    request: Request,
    proposal_id: str,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """Render the HITL-06 second-channel confirm page (UI-SPEC §3b).

    The operator lands here from the Slack DM URL. Renders the
    ``first_live_confirm.html.j2`` full-page template with the trade
    detail panel + two checkboxes + 5s countdown. Form POSTs back to
    this same path.
    """
    from gekko.schemas.proposal import TradeProposal

    engine = request.app.state.engine

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
    user_id: str = Depends(require_session),
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

    engine = request.app.state.engine

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

        try:
            await transition_status(
                session,
                proposal_id,
                from_status="AWAITING_2ND_CHANNEL",
                to_status="APPROVED_LIVE",
            )
        except ValueError as exc:
            # Race condition: sweep or another click resolved the proposal
            # between the status-check guard above and this transition.
            # Propagate as an HTTP 409 so the operator's browser shows a
            # clear message rather than a 500. (Pitfall 9 caller-gate.)
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
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


__all__: tuple[str, ...] = ("public_router", "router")
