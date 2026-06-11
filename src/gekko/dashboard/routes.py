"""FastAPI dashboard routes — Plan 01-09 Task 3 (STRAT-02, REG-01, REG-03, REG-04).

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
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from gekko.config import get_settings
from gekko.db.models import Strategy as StrategyRow
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy, next_version

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


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
    # template doesn't need to deserialize Pydantic models.
    enriched: list[dict[str, object]] = []
    for r in rows:
        try:
            strategy = Strategy.model_validate_json(r.payload_json)
            preview = ", ".join(strategy.watchlist[:5])
            if len(strategy.watchlist) > 5:
                preview += f", … (+{len(strategy.watchlist) - 5})"
        except Exception:
            preview = "(payload not parseable)"
        enriched.append(
            {
                "strategy_name": r.strategy_name,
                "version": r.version,
                "watchlist_preview": preview,
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


__all__: tuple[str, ...] = ("router",)
