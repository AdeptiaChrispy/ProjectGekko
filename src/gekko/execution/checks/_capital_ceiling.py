"""Per-strategy capital ceiling — Plan 05-03 Task 1 (TRUST-03 / SC-3).

Caps the strategy's TOTAL DEPLOYED CAPITAL (sum of open positions for this
strategy's watchlist tickers + this order's notional) to the per-strategy
``StrategyMetadata.capital_ceiling_usd`` (server_default "1000.00" per D-T16).
Stacks with ``max_position_pct`` (per-strategy) and the account-wide portfolio
caps. Runs inside the single ``OrderGuard.place_order`` pipeline so every order —
auto or HITL — inherits it (D-T08).

**Alpaca position netting:** a single Alpaca account nets one position per
ticker. "This strategy's deployed capital" is the pragmatic attribution: sum the
account's net positions for tickers in ``strategy.watchlist``. The check reads a
SINGLE ``get_positions()`` call (no per-strategy fan-out).

Lowering the ceiling is unconstrained (de-risking is always safe); only the
INCREASE confirmation lives in ``trust.py`` / the route, never here. A SELL never
trips the ceiling because it reduces deployment.

Decimal-exact throughout. Mirrors ``_check_position_pct`` in ``_hard_caps.py``.

No Agent-SDK import in this module — enforced by the grep gate over
``execution/checks/*.py`` (LLM bytes never reach a cap guard).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.brokers.base import Brokerage, OrderRequest
from gekko.config import get_settings
from gekko.core.errors import OrderGuardRejected
from gekko.core.types import OrderSide, OrderType
from gekko.db.engine import get_async_engine
from gekko.db.models import StrategyMetadata
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.execution.checks._hard_caps import _ref_price_for
from gekko.logging_config import get_logger
from gekko.schemas.strategy import Strategy
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: Read at this default when the StrategyMetadata row is missing or the
#: ceiling column is NULL (D-T16).
DEFAULT_CAPITAL_CEILING_USD = Decimal("1000.00")


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Per-user session factory + engine (mirrors PATTERNS §3c seam)."""
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


async def _load_ceiling(*, user_id: str, strategy_name: str) -> Decimal:
    """Load ``capital_ceiling_usd`` or fall back to the $1,000 default."""
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            meta = await session.get(
                StrategyMetadata, (user_id, strategy_name)
            )
            if meta is None or meta.capital_ceiling_usd is None:
                return DEFAULT_CAPITAL_CEILING_USD
            raw = str(meta.capital_ceiling_usd).strip().strip("'\"")
            if not raw:
                return DEFAULT_CAPITAL_CEILING_USD
            try:
                return Decimal(raw)
            except Exception:  # noqa: BLE001 - default on parse failure
                return DEFAULT_CAPITAL_CEILING_USD
    finally:
        if engine is not None:
            await engine.dispose()


async def check_capital_ceiling(
    *,
    req: OrderRequest,
    strategy: Strategy,
    broker: Brokerage,
    user_id: str,
) -> None:
    """Reject when deployed capital + this order exceeds the strategy ceiling.

    Deployed capital = Σ(market_value) of the account's net positions whose
    ticker is in ``strategy.watchlist``. A SELL contributes zero proposed
    notional (de-risking is always allowed).
    """
    ceiling = await _load_ceiling(
        user_id=user_id, strategy_name=strategy.name
    )
    if ceiling <= Decimal("0"):
        # A zero ceiling means "no new deployment"; let it block any buy via
        # the normal comparison below (deployed + notional > 0 >= 0 only when
        # something is proposed). Guard against negative defensively.
        ceiling = Decimal("0")

    watchlist = {str(t).upper() for t in strategy.watchlist}

    positions = await broker.get_positions()
    deployed = Decimal("0")
    for pos in positions:
        sym = str(pos.get("symbol") or pos.get("asset_id") or "").upper()
        if sym not in watchlist:
            continue
        market_value_raw = pos.get("market_value") or pos.get("cost_basis") or "0"
        deployed += Decimal(str(market_value_raw))

    # Proposed BUY notional; a SELL reduces deployment → zero contribution.
    proposed_notional = Decimal("0")
    if req.side is OrderSide.BUY:
        quote: dict[str, Any] | None = None
        if req.order_type is OrderType.MARKET:
            try:
                quote = await broker.get_quote(req.symbol)
            except Exception:  # noqa: BLE001 - best-effort price
                quote = None
        ref_price = _ref_price_for(req, quote)
        if ref_price > Decimal("0"):
            proposed_notional = req.qty * ref_price

    # The ceiling bounds NEW deployment. An order that does not add notional
    # (a SELL, or a BUY we could not price) can never push deployment higher,
    # so it is always allowed — even when the account is already over-ceiling
    # (de-risking must never be blocked, D-T14).
    if proposed_notional <= Decimal("0"):
        return

    total_after = deployed + proposed_notional
    if total_after > ceiling:
        raise OrderGuardRejected(
            "capital_ceiling",
            (
                f"deployed capital after this order {total_after} exceeds "
                f"capital_ceiling_usd {ceiling} for strategy "
                f"{strategy.name!r}"
            ),
            extra={
                "ticker": req.symbol,
                "strategy_name": strategy.name,
                "deployed_after": str(total_after),
                "deployed_before": str(deployed),
                "proposed_notional": str(proposed_notional),
                "cap": str(ceiling),
            },
        )


__all__: tuple[str, ...] = ("DEFAULT_CAPITAL_CEILING_USD", "check_capital_ceiling")
