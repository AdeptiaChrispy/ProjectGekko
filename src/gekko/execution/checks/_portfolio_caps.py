"""Portfolio aggregate caps — Plan 05-03 Task 1 (TRUST-02 / SC-2).

Account-wide caps that STACK on top of each strategy's own hard caps and apply
to EVERY order — auto-executed and human-approved alike (D-T08). They run inside
the single ``OrderGuard.place_order`` pipeline, after ``check_hard_caps`` and
before ``check_qty_price_sanity``; the LLM cannot reason past them because they
are deterministic Python guards on the last line before broker submission.

The four caps (all user-level config on the ``users`` row, added in Plan 01;
each stored as TEXT — percent as a FRACTION string "0.50", USD as "200.00";
blank/NULL = DISABLED → early return):

  1. ``max_total_exposure_pct``       → ``portfolio_total_exposure``
  2. ``max_sector_concentration_pct`` → ``portfolio_sector_concentration``
  3. ``max_correlated_ticker_pct``    → ``portfolio_correlated_ticker``
  4. ``max_total_daily_loss_usd``     → ``portfolio_daily_loss``

**Alpaca position netting (RESEARCH Pitfall 4 / Open Q #4):** a single Alpaca
account holds ONE net position per ticker, so this module aggregates over a
SINGLE ``get_positions()`` call and NEVER issues N×M per-strategy broker calls.
The "correlated-ticker / same-ticker across strategies" cap measures the
account's single net per-ticker position against ``max_correlated_ticker_pct``.

Decimal-exact math throughout (no binary-fp). Mirrors ``_hard_caps.py`` exactly:
the ``_get_session_factory`` shim, ``_ref_price_for`` pricing helper, the
``get_account()`` / ``get_positions()`` aggregation shape, the best-effort
``_resolve_sector`` lookup with the >25-position perf canary, and the
``equity <= 0`` early-return guards.

No Agent-SDK import in this module — enforced by the Phase-1/2 grep gate
covering ``execution/checks/*.py`` (LLM bytes never reach a cap guard).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.brokers.base import Brokerage, OrderRequest
from gekko.config import get_settings
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType
from gekko.db.engine import get_async_engine
from gekko.db.models import Event, User
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.execution.checks._hard_caps import _ref_price_for, _resolve_sector
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


# ---------------------------------------------------------------------------
# Cap loading
# ---------------------------------------------------------------------------


class _PortfolioCaps:
    """Parsed user-level portfolio caps. ``None`` means the cap is disabled."""

    __slots__ = (
        "max_correlated_ticker_pct",
        "max_sector_concentration_pct",
        "max_total_daily_loss_usd",
        "max_total_exposure_pct",
    )

    def __init__(
        self,
        *,
        max_total_exposure_pct: Decimal | None,
        max_sector_concentration_pct: Decimal | None,
        max_correlated_ticker_pct: Decimal | None,
        max_total_daily_loss_usd: Decimal | None,
    ) -> None:
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_sector_concentration_pct = max_sector_concentration_pct
        self.max_correlated_ticker_pct = max_correlated_ticker_pct
        self.max_total_daily_loss_usd = max_total_daily_loss_usd

    @property
    def all_disabled(self) -> bool:
        return (
            self.max_total_exposure_pct is None
            and self.max_sector_concentration_pct is None
            and self.max_correlated_ticker_pct is None
            and self.max_total_daily_loss_usd is None
        )


def _parse_cap(raw: str | None) -> Decimal | None:
    """Parse a TEXT cap column into a Decimal, or ``None`` when disabled.

    Blank/NULL/unparseable → ``None`` (disabled). The stored value carries the
    money-as-TEXT convention; defensively strip any stray quote characters a
    bad server_default may have left (mirrors the settings_get defensive parse).
    """
    if raw is None:
        return None
    cleaned = raw.strip().strip("'\"")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:  # noqa: BLE001 - disabled on any parse failure
        return None


async def _load_portfolio_caps(user_id: str) -> _PortfolioCaps:
    """Load the four user-level portfolio caps from the ``users`` row."""
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            user = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).scalar_one_or_none()
            if user is None:
                return _PortfolioCaps(
                    max_total_exposure_pct=None,
                    max_sector_concentration_pct=None,
                    max_correlated_ticker_pct=None,
                    max_total_daily_loss_usd=None,
                )
            return _PortfolioCaps(
                max_total_exposure_pct=_parse_cap(user.max_total_exposure_pct),
                max_sector_concentration_pct=_parse_cap(
                    user.max_sector_concentration_pct
                ),
                max_correlated_ticker_pct=_parse_cap(
                    user.max_correlated_ticker_pct
                ),
                max_total_daily_loss_usd=_parse_cap(
                    user.max_total_daily_loss_usd
                ),
            )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Shared pricing
# ---------------------------------------------------------------------------


async def _proposed_buy_notional(
    *, req: OrderRequest, broker: Brokerage
) -> Decimal:
    """Notional of the proposed order for cap math.

    A SELL reduces deployed exposure, so its contribution to every aggregate
    exposure cap is zero (the cap bounds NEW deployment, never de-risking).
    """
    if req.side is OrderSide.SELL:
        return Decimal("0")
    quote: dict[str, Any] | None = None
    if req.order_type is OrderType.MARKET:
        try:
            quote = await broker.get_quote(req.symbol)
        except Exception:  # noqa: BLE001 - best-effort price
            quote = None
    ref_price = _ref_price_for(req, quote)
    if ref_price <= Decimal("0"):
        return Decimal("0")
    return req.qty * ref_price


def _today_utc_window() -> tuple[str, str]:
    """Return the (start_iso, end_iso) ISO-8601 strings bracketing today UTC."""
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


# ---------------------------------------------------------------------------
# Individual caps
# ---------------------------------------------------------------------------


def _check_total_exposure(
    *,
    cap: Decimal,
    equity: Decimal,
    positions: list[dict[str, Any]],
    proposed_notional: Decimal,
    symbol: str,
) -> None:
    current = Decimal("0")
    for pos in positions:
        market_value_raw = pos.get("market_value") or pos.get("cost_basis") or "0"
        current += Decimal(str(market_value_raw))
    total_after = current + proposed_notional
    pct_after = total_after / equity
    if pct_after > cap:
        raise OrderGuardRejected(
            "portfolio_total_exposure",
            (
                f"portfolio exposure after this order {total_after} / equity "
                f"{equity} = {pct_after * Decimal('100'):.4f}% exceeds "
                f"max_total_exposure_pct {cap * Decimal('100'):.4f}%"
            ),
            extra={
                "ticker": symbol,
                "total_exposure_after": str(total_after),
                "equity": str(equity),
                "actual_pct": str(pct_after),
                "cap": str(cap),
            },
        )


def _check_correlated_ticker(
    *,
    cap: Decimal,
    equity: Decimal,
    positions: list[dict[str, Any]],
    proposed_notional: Decimal,
    symbol: str,
) -> None:
    # Alpaca nets ONE position per ticker; sum any rows matching the symbol
    # (defensive — there should be at most one).
    ticker_value = Decimal("0")
    for pos in positions:
        sym = pos.get("symbol") or pos.get("asset_id") or ""
        if str(sym) != symbol:
            continue
        market_value_raw = pos.get("market_value") or pos.get("cost_basis") or "0"
        ticker_value += Decimal(str(market_value_raw))
    total_after = ticker_value + proposed_notional
    pct_after = total_after / equity
    if pct_after > cap:
        raise OrderGuardRejected(
            "portfolio_correlated_ticker",
            (
                f"net {symbol} position after this order {total_after} / equity "
                f"{equity} = {pct_after * Decimal('100'):.4f}% exceeds "
                f"max_correlated_ticker_pct {cap * Decimal('100'):.4f}%"
            ),
            extra={
                "ticker": symbol,
                "ticker_exposure_after": str(total_after),
                "equity": str(equity),
                "actual_pct": str(pct_after),
                "cap": str(cap),
            },
        )


async def _check_sector_concentration(
    *,
    cap: Decimal,
    equity: Decimal,
    positions: list[dict[str, Any]],
    proposed_notional: Decimal,
    broker: Brokerage,
    symbol: str,
) -> None:
    target_sector = await _resolve_sector(broker, symbol)
    if target_sector is None:
        # Best-effort: when the order's sector is unknown the other three caps
        # bound exposure independently (mirrors _hard_caps sector skip).
        log.warning(
            "orderguard.portfolio_sector_lookup_skipped",
            ticker=symbol,
            reason="sector_unknown",
        )
        return

    if len(positions) > 25:
        log.warning(
            "orderguard.portfolio_sector_resolve_loop_long",
            ticker=symbol,
            position_count=len(positions),
            threshold=25,
            note=(
                "Per-position get_asset loop; batch via get_all_assets "
                "if this fires routinely."
            ),
        )

    # Cache sector lookups within this single invocation (Pitfall 4 / T-05-14).
    sector_cache: dict[str, str | None] = {symbol: target_sector}
    sector_exposure = Decimal("0")
    for pos in positions:
        sym = str(pos.get("symbol") or pos.get("asset_id") or "")
        if not sym:
            continue
        if sym not in sector_cache:
            sector_cache[sym] = await _resolve_sector(broker, sym)
        if sector_cache[sym] != target_sector:
            continue
        market_value_raw = pos.get("market_value") or pos.get("cost_basis") or "0"
        sector_exposure += Decimal(str(market_value_raw))

    total_after = sector_exposure + proposed_notional
    pct_after = total_after / equity
    if pct_after > cap:
        raise OrderGuardRejected(
            "portfolio_sector_concentration",
            (
                f"sector {target_sector!r} exposure after this order "
                f"{total_after} / {equity} = {pct_after * Decimal('100'):.4f}% "
                f"exceeds max_sector_concentration_pct "
                f"{cap * Decimal('100'):.4f}%"
            ),
            extra={
                "ticker": symbol,
                "sector": target_sector,
                "sector_exposure_after": str(total_after),
                "equity": str(equity),
                "actual_pct": str(pct_after),
                "cap": str(cap),
            },
        )


async def _check_daily_loss(
    *,
    cap: Decimal,
    user_id: str,
    symbol: str,
) -> None:
    start_iso, end_iso = _today_utc_window()
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event)
                    .where(
                        Event.user_id == user_id,
                        Event.event_type == "fill",
                        Event.ts >= start_iso,
                        Event.ts <= end_iso,
                    )
                    .limit(1000)
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    if len(rows) >= 1000:
        log.warning(
            "orderguard.portfolio_daily_loss_scan_at_limit",
            user_id=user_id,
            row_count=len(rows),
            limit=1000,
        )

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
            cumulative_loss += -pnl  # positive magnitude

    if cumulative_loss >= cap:
        raise OrderGuardRejected(
            "portfolio_daily_loss",
            (
                f"portfolio-wide realized loss today {cumulative_loss} >= cap "
                f"max_total_daily_loss_usd {cap}"
            ),
            extra={
                "ticker": symbol,
                "cumulative_loss_usd": str(cumulative_loss),
                "cap": str(cap),
            },
        )


# ---------------------------------------------------------------------------
# Composed entry point
# ---------------------------------------------------------------------------


async def check_portfolio_caps(
    *,
    req: OrderRequest,
    strategy: Strategy,  # noqa: ARG001 - portfolio caps are account-wide, not per-strategy
    broker: Brokerage,
    user_id: str,
) -> None:
    """Run the four account-wide portfolio caps (D-T08 / SC-2).

    Aggregates over a SINGLE ``get_positions()`` call (Alpaca nets one position
    per ticker — never per-strategy fan-out). Any NULL/blank cap is disabled.
    The first breach short-circuits with its dedicated reject_code.
    """
    caps = await _load_portfolio_caps(user_id)
    if caps.all_disabled:
        return

    # Equity once; positions once (no per-strategy broker fan-out).
    account = await broker.get_account()
    equity_raw = account.get("equity") or account.get("portfolio_value") or "0"
    equity = Decimal(str(equity_raw))
    if equity <= Decimal("0"):
        # Zero equity = no exposure caps to enforce; broker rejects downstream.
        # The daily-loss cap is independent of equity, so still evaluate it.
        if caps.max_total_daily_loss_usd is not None:
            await _check_daily_loss(
                cap=caps.max_total_daily_loss_usd,
                user_id=user_id,
                symbol=req.symbol,
            )
        return

    positions = await broker.get_positions()
    proposed_notional = await _proposed_buy_notional(req=req, broker=broker)

    if caps.max_total_exposure_pct is not None:
        _check_total_exposure(
            cap=caps.max_total_exposure_pct,
            equity=equity,
            positions=positions,
            proposed_notional=proposed_notional,
            symbol=req.symbol,
        )
    if caps.max_sector_concentration_pct is not None:
        await _check_sector_concentration(
            cap=caps.max_sector_concentration_pct,
            equity=equity,
            positions=positions,
            proposed_notional=proposed_notional,
            broker=broker,
            symbol=req.symbol,
        )
    if caps.max_correlated_ticker_pct is not None:
        _check_correlated_ticker(
            cap=caps.max_correlated_ticker_pct,
            equity=equity,
            positions=positions,
            proposed_notional=proposed_notional,
            symbol=req.symbol,
        )
    if caps.max_total_daily_loss_usd is not None:
        await _check_daily_loss(
            cap=caps.max_total_daily_loss_usd,
            user_id=user_id,
            symbol=req.symbol,
        )


__all__: tuple[str, ...] = ("check_portfolio_caps",)
