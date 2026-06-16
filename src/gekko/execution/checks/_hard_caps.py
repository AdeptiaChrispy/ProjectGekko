"""Hard-caps check — Plan 02-02 Task 2 (D-29 / EXEC-04).

The 4 hard caps (Pydantic-validated bounds on the :class:`HardCaps` model)
that every approved order must respect:

  1. ``max_position_pct`` — proposed position value / account equity
  2. ``max_daily_loss_usd`` — cumulative realized P&L today
  3. ``max_trades_per_day`` — count of today's ``order_submitted`` events
  4. ``max_sector_exposure_pct`` — existing + proposed sector exposure

Reject ordering (deterministic for tests): position_pct → daily_loss →
trades_per_day → sector_exposure. The first rejection short-circuits.

Sector resolution is best-effort: ``broker._wrapped._client.get_asset(symbol)
.attributes`` carries the alpaca-py asset attributes including the sector
classification for US equities. When the lookup fails (broker disconnected,
asset not classified, or the call raises) the sector check is SKIPPED (logged
warning) rather than rejecting — per RESEARCH §1 Open Question — because the
other three caps are independently sufficient to bound exposure.

Decimal-exact math throughout per PATTERNS §3b (no binary-fp).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.brokers.base import Brokerage, OrderRequest
from gekko.config import get_settings
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderType
from gekko.db.engine import get_async_engine
from gekko.db.models import Event
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.schemas.strategy import Strategy
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Per-user session factory + engine (mirrors PATTERNS §3c seam)."""
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


def _ref_price_for(req: OrderRequest, quote: dict[str, Any] | None) -> Decimal:
    """Pick the reference price for the position-pct math.

    LIMIT uses ``req.limit_price``, STOP uses ``req.stop_price``, MARKET
    falls back to the quote's ``ask_price`` (or ``ap``). Returns
    ``Decimal('0')`` when nothing is available — caller decides whether
    to reject or skip.
    """
    if req.order_type is OrderType.LIMIT and req.limit_price is not None:
        return req.limit_price
    if req.order_type is OrderType.STOP and req.stop_price is not None:
        return req.stop_price
    if quote is not None:
        raw = quote.get("ask_price")
        if raw is None:
            raw = quote.get("ap")
        if raw is not None:
            return Decimal(str(raw))
    return Decimal("0")


async def _check_position_pct(
    *,
    req: OrderRequest,
    strategy: Strategy,
    broker: Brokerage,
) -> None:
    account = await broker.get_account()
    equity_raw = account.get("equity") or account.get("portfolio_value") or "0"
    equity = Decimal(str(equity_raw))
    if equity <= Decimal("0"):
        # Zero equity = no caps to enforce here; let the broker reject the
        # order downstream with the canonical "insufficient buying power".
        return

    quote: dict[str, Any] | None = None
    if req.order_type is OrderType.MARKET:
        try:
            quote = await broker.get_quote(req.symbol)
        except Exception:  # noqa: BLE001 - best-effort price
            quote = None
    ref_price = _ref_price_for(req, quote)
    if ref_price <= Decimal("0"):
        # Cannot price the position — skip this cap and let the qty_price
        # sanity check downstream do its job. (No false-positive reject.)
        return

    proposed_notional = req.qty * ref_price
    actual_pct = proposed_notional / equity
    cap = strategy.hard_caps.max_position_pct
    if actual_pct > cap:
        raise OrderGuardRejected(
            "hard_cap_position_pct",
            (
                f"proposed position {proposed_notional} / equity {equity} = "
                f"{actual_pct * Decimal('100'):.4f}% exceeds "
                f"max_position_pct {cap * Decimal('100'):.4f}%"
            ),
            extra={
                "ticker": req.symbol,
                "proposed_notional": str(proposed_notional),
                "equity": str(equity),
                "actual_pct": str(actual_pct),
                "cap": str(cap),
            },
        )


def _today_utc_window() -> tuple[str, str]:
    """Return the (start_iso, end_iso) ISO-8601 strings bracketing today UTC."""
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


async def _check_daily_loss(
    *,
    req: OrderRequest,
    strategy: Strategy,
    user_id: str,
) -> None:
    start_iso, end_iso = _today_utc_window()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            # ``payload_json`` is the canonical-subset JSON; the realized
            # P&L payload key is `realized_pnl_usd` if a fill event carries
            # it. The Phase-1 fill payload (executor.on_fill_event) does
            # NOT yet carry realized_pnl_usd, so this check is forward-
            # compatible: in Phase 1 the sum is always 0 and the cap never
            # fires. Plan 02-03's wash-sale + future cost-basis work will
            # populate this key.
            rows = (
                await session.execute(
                    select(Event).where(
                        Event.user_id == user_id,
                        Event.event_type == "fill",
                        Event.ts >= start_iso,
                        Event.ts <= end_iso,
                    )
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    cumulative_loss = Decimal("0")
    for row in rows:
        try:
            outer = json.loads(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        payload = outer.get("payload", outer)
        pnl_raw = payload.get("realized_pnl_usd")
        if pnl_raw is None:
            continue
        try:
            pnl = Decimal(str(pnl_raw))
        except Exception:  # noqa: BLE001 - skip malformed
            continue
        if pnl < Decimal("0"):
            cumulative_loss += -pnl  # store as positive magnitude

    cap = strategy.hard_caps.max_daily_loss_usd
    if cumulative_loss >= cap:
        raise OrderGuardRejected(
            "hard_cap_daily_loss",
            (
                f"cumulative realized loss today {cumulative_loss} >= cap "
                f"max_daily_loss_usd {cap}"
            ),
            extra={
                "ticker": req.symbol,
                "cumulative_loss_usd": str(cumulative_loss),
                "cap": str(cap),
            },
        )


async def _check_trades_per_day(
    *,
    req: OrderRequest,
    strategy: Strategy,
    user_id: str,
) -> None:
    start_iso, end_iso = _today_utc_window()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event).where(
                        Event.user_id == user_id,
                        Event.event_type == "order_submitted",
                        Event.ts >= start_iso,
                        Event.ts <= end_iso,
                    )
                )
            ).scalars().all()
            count = len(rows)
    finally:
        if engine is not None:
            await engine.dispose()

    cap = strategy.hard_caps.max_trades_per_day
    if count >= cap:
        raise OrderGuardRejected(
            "hard_cap_trades_per_day",
            (
                f"trades already submitted today {count} >= cap "
                f"max_trades_per_day {cap}"
            ),
            extra={
                "ticker": req.symbol,
                "trades_today": count,
                "cap": cap,
            },
        )


async def _resolve_sector(broker: Brokerage, symbol: str) -> str | None:
    """Best-effort sector lookup via alpaca-py's Asset.attributes shape.

    Returns ``None`` when the underlying client lacks ``get_asset`` (e.g.,
    a MagicMock broker in unit tests) OR the asset attributes don't carry
    a sector classification. Sector check is then SKIPPED — see module
    docstring.
    """
    wrapped_client = getattr(broker, "_client", None)
    if wrapped_client is None:
        return None
    get_asset = getattr(wrapped_client, "get_asset", None)
    if get_asset is None:
        return None
    try:
        asset = await asyncio.to_thread(get_asset, symbol)
    except Exception:  # noqa: BLE001 - best-effort
        return None
    attrs = getattr(asset, "attributes", None)
    if attrs is None and isinstance(asset, dict):
        attrs = asset.get("attributes")
    if attrs is None:
        return None
    # alpaca-py exposes attributes as a list of strings; older shapes as
    # a dict. Either way, look for a 'sector' classification.
    if isinstance(attrs, dict):
        sector = attrs.get("sector")
        return str(sector) if sector else None
    if isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, str) and item.startswith("sector:"):
                return item.split(":", 1)[1]
    return None


async def _check_sector_exposure(
    *,
    req: OrderRequest,
    strategy: Strategy,
    broker: Brokerage,
) -> None:
    sector = await _resolve_sector(broker, req.symbol)
    if sector is None:
        log.warning(
            "orderguard.sector_lookup_skipped",
            ticker=req.symbol,
            reason="sector_unknown",
        )
        return

    account = await broker.get_account()
    equity = Decimal(str(account.get("equity") or "0"))
    if equity <= Decimal("0"):
        return

    positions = await broker.get_positions()
    sector_exposure = Decimal("0")
    for pos in positions:
        sym = pos.get("symbol") or pos.get("asset_id") or ""
        pos_sector = await _resolve_sector(broker, str(sym))
        if pos_sector != sector:
            continue
        market_value_raw = pos.get("market_value") or pos.get("cost_basis") or "0"
        sector_exposure += Decimal(str(market_value_raw))

    quote: dict[str, Any] | None = None
    if req.order_type is OrderType.MARKET:
        try:
            quote = await broker.get_quote(req.symbol)
        except Exception:  # noqa: BLE001 - best-effort
            quote = None
    ref_price = _ref_price_for(req, quote)
    proposed_notional = req.qty * ref_price if ref_price > Decimal("0") else Decimal("0")

    total_after = sector_exposure + proposed_notional
    pct_after = total_after / equity
    cap = strategy.hard_caps.max_sector_exposure_pct
    if pct_after > cap:
        raise OrderGuardRejected(
            "hard_cap_sector_exposure",
            (
                f"sector {sector!r} exposure after this order "
                f"{total_after} / {equity} = {pct_after * Decimal('100'):.4f}% "
                f"exceeds max_sector_exposure_pct "
                f"{cap * Decimal('100'):.4f}%"
            ),
            extra={
                "ticker": req.symbol,
                "sector": sector,
                "sector_exposure_after": str(total_after),
                "equity": str(equity),
                "actual_pct": str(pct_after),
                "cap": str(cap),
            },
        )


async def check_hard_caps(
    *,
    req: OrderRequest,
    strategy: Strategy,
    broker: Brokerage,
    user_id: str,
) -> None:
    """Run all 4 hard-cap sub-checks in deterministic order.

    Order is: position_pct → daily_loss → trades_per_day → sector_exposure.
    The first rejection short-circuits.
    """
    await _check_position_pct(req=req, strategy=strategy, broker=broker)
    await _check_daily_loss(req=req, strategy=strategy, user_id=user_id)
    await _check_trades_per_day(req=req, strategy=strategy, user_id=user_id)
    await _check_sector_exposure(req=req, strategy=strategy, broker=broker)


__all__: tuple[str, ...] = ("check_hard_caps",)
