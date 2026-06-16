"""Wash-sale FLAG path — Plan 02-03 Task 3 (D-28 / EXEC-09 / IRC §1091).

FLAG-ONLY contract — this function NEVER raises ``OrderGuardRejected``.
Per EXEC-09 + D-29: wash-sale detection is a HITL surface, not a BLOCK.
The user owns the tax decision; the agent surfaces a warning line in the
Slack Block Kit card and lets the user click Approve with disclosure.

IRC §1091 disallows a loss on a security sale when a "substantially
identical" security is purchased within 30 days before OR after the
sale (a 61-day window centered on the sale). The disallowed loss isn't
permanently lost — it's added to the cost basis of the replacement
shares — but for current-year tax reporting, it's deferred.

**P2 simplification (RESEARCH §5):** same-ticker exact match within the
past 30 days. "Substantially identical" (same-index ETFs, options on
same stock, near-identical companies) is P4+ refinement.

The detection walks the per-user ``events`` table for ``fill`` events
in the rolling 30-calendar-day window, returns the FIRST same-ticker
match as a flag dict. Bounded scan: ``LIMIT 100`` events to keep the
worst-case scan cheap (RESEARCH §5).

Returned dict shape (LOCKED — tests in
``tests/unit/test_wash_sale_flag.py`` lock the key set):

.. code-block:: python

    {
        "would_be_wash_sale": True,
        "lookback_event_id": <int>,    # audit-row PK
        "lookback_date": "<iso>",      # ISO-8601 timestamp string
        "ticker": "<UPPERCASE>",
        "lookback_qty": "<str>",       # filled_qty as string
        "lookback_side": "buy"|"sell", # whichever was seeded
        "note": "<human-readable explainer>",
    }

References:
  * .planning/phases/02-orderguard.../02-RESEARCH.md  §5 (FLAG path)
  * .planning/phases/02-orderguard.../02-PATTERNS.md  §1a row 10
  * https://www.fidelity.com/learning-center/personal-finance/wash-sales-rules-tax
  * https://legalclarity.org/the-wash-sale-rule-irc-section-1091-explained/
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.brokers.base import OrderRequest
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Event
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: IRC §1091 wash-sale window — 30 calendar days before OR after the sale.
_WASH_SALE_WINDOW_DAYS = 30

#: Bounded scan limit — RESEARCH §5. 100 events covers a typical user's
#: weekly fill volume comfortably; the LIMIT keeps the worst-case scan
#: cost predictable.
_SCAN_LIMIT = 100


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Per-user session factory + engine (PATTERNS §3c test seam)."""
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


async def flag_wash_sale(
    *,
    req: OrderRequest,
    user_id: str,
) -> dict[str, Any] | None:
    """Return a flag dict if this trade may create a wash sale, else None.

    NEVER raises. The function signature ``dict | None`` is the contract
    per PATTERNS §4 anti-pattern row 12 — wash-sale enforcement is
    EXPLICITLY FLAG-only.

    :param req: The :class:`OrderRequest` (or a TradeProposal-shaped object
        with a ``symbol`` attribute). The flag is generated for the
        ticker about to be traded.
    :param user_id: Per-user SQLCipher DB scope.
    :returns: A flag dict (see module docstring for shape) when a same-
        ticker fill exists in the past 30 days; ``None`` otherwise.
    """
    try:
        return await _flag_wash_sale_inner(req=req, user_id=user_id)
    except Exception:  # noqa: BLE001 - tripwire: NEVER raises (EXEC-09)
        # Any internal failure (DB connection error, malformed payload,
        # etc.) MUST NOT propagate. Log and return None — the wash-sale
        # FLAG is informational; an unflagged proposal is the safe
        # default state.
        log.warning(
            "wash_sale.flag_failed",
            user_id=user_id,
            ticker=getattr(req, "symbol", None),
        )
        return None


async def _flag_wash_sale_inner(
    *,
    req: OrderRequest,
    user_id: str,
) -> dict[str, Any] | None:
    """Inner implementation — wrapped by :func:`flag_wash_sale` exception guard."""
    window_start = (
        datetime.now(UTC) - timedelta(days=_WASH_SALE_WINDOW_DAYS)
    ).isoformat()
    req_ticker = req.symbol.upper()

    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            rows = (
                await session.execute(
                    select(Event)
                    .where(
                        Event.user_id == user_id,
                        Event.event_type == "fill",
                        Event.ts >= window_start,
                    )
                    .order_by(Event.id.desc())
                    .limit(_SCAN_LIMIT)
                )
            ).scalars().all()
    finally:
        if engine is not None:
            await engine.dispose()

    for row in rows:
        try:
            outer = json.loads(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        # canonical-subset shape from append_event:
        # {event_type, payload, ts, user_id}
        payload = outer.get("payload", outer)
        if not isinstance(payload, dict):
            continue
        ticker_raw = payload.get("ticker") or payload.get("symbol")
        if not ticker_raw:
            continue
        if str(ticker_raw).upper() != req_ticker:
            continue
        # First same-ticker match within window — return the flag.
        return {
            "would_be_wash_sale": True,
            "lookback_event_id": row.id,
            "lookback_date": row.ts,
            "ticker": req_ticker,
            "lookback_qty": str(payload.get("filled_qty", "")),
            "lookback_side": str(payload.get("side", "unknown")),
            "note": (
                "A trade in this ticker within the past 30 days may "
                "create a wash sale (IRC §1091) — losses disallowed for "
                "current-year tax. The trade is allowed; review the "
                "lookback event before approving."
            ),
        }
    return None


__all__: tuple[str, ...] = ("flag_wash_sale",)
