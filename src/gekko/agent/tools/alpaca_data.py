"""``get_quote`` Researcher tool — RES-01 — Plan 01-07 Task 3.

Wraps the Alpaca IEX latest-quote endpoint via the project's
:class:`gekko.brokers.base.Brokerage` ABC. On any failure, falls back to
``yahooquery`` so the Researcher subagent always gets *some* price signal
(graceful degradation per RESEARCH §"Environment Availability").

Per docs/sdk-shape.md delta #1 and #2:

* ``@tool("get_quote", "...", {"ticker": str})`` — positional decorator,
  short tool name (the SDK prefixes ``mcp__gekko__`` at registration time).
* Function signature is ``async def get_quote(args: dict) -> dict``, returns
  the MCP content shape ``{"content": [{"type": "text", "text": json_str}]}``.

The function does NOT take a broker kwarg — it pulls the broker (and the
BudgetTracker) from the module-global tool context (see
``gekko.agent.tools.context``).

References:
  * .planning/.../01-RESEARCH.md  §"Token-cost estimates" → 100 tokens
  * docs/sdk-shape.md             deltas #1, #2
  * src/gekko/schemas/research.py TickerSnapshot
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from claude_agent_sdk import tool

from gekko.agent.tools.context import get_tool_context
from gekko.core.errors import BrokerOrderError
from gekko.logging_config import get_logger
from gekko.schemas.research import TickerSnapshot

log = get_logger(__name__)

#: Token-cost estimate per ``get_quote`` invocation. Refined in P4 from
#: the SDK's ``ResultMessage.usage`` figures (docs/sdk-shape.md #6).
_TOKEN_COST: int = 100


def _build_snapshot_from_broker(ticker: str, raw: dict[str, Any]) -> TickerSnapshot:
    """Coerce a broker quote dict into a :class:`TickerSnapshot`.

    The Alpaca quote dict carries ``ask_price`` / ``bid_price`` /
    ``timestamp`` keys plus a handful of others. We pull the load-bearing
    fields and let Pydantic validate.
    """
    ask = raw.get("ask_price")
    bid = raw.get("bid_price")
    ts = raw.get("timestamp") or datetime.now(UTC).isoformat()
    # The "last" price is unavailable from a quote-only endpoint; we
    # approximate via mid-quote, falling back to whichever side exists.
    if ask is not None and bid is not None:
        last = (Decimal(str(ask)) + Decimal(str(bid))) / Decimal("2")
    elif ask is not None:
        last = Decimal(str(ask))
    elif bid is not None:
        last = Decimal(str(bid))
    else:
        msg = f"broker quote for {ticker} missing both bid and ask"
        raise BrokerOrderError(msg)

    return TickerSnapshot(
        ticker=ticker,
        last_price=last,
        bid=Decimal(str(bid)) if bid is not None else None,
        ask=Decimal(str(ask)) if ask is not None else None,
        quote_ts=str(ts),
    )


def _build_snapshot_from_yahooquery(ticker: str) -> TickerSnapshot:
    """Fall back to ``yahooquery`` if the broker quote path fails.

    Imported lazily so test-mode environments without yahooquery wheels
    can still run the broker-only path.
    """
    from yahooquery import Ticker

    yq = Ticker(ticker)
    quotes = yq.quotes
    if not isinstance(quotes, dict) or ticker not in quotes:
        msg = f"yahooquery returned no quote for {ticker}"
        raise BrokerOrderError(msg)
    row = quotes[ticker]
    # yahooquery emits floats — coerce via str() per the EXEC-01 grep gate.
    last_raw = row.get("regularMarketPrice") or row.get("postMarketPrice")
    if last_raw is None:
        msg = f"yahooquery quote for {ticker} missing regularMarketPrice"
        raise BrokerOrderError(msg)
    bid_raw = row.get("bid")
    ask_raw = row.get("ask")
    return TickerSnapshot(
        ticker=ticker,
        last_price=Decimal(str(last_raw)),
        bid=Decimal(str(bid_raw)) if bid_raw not in (None, 0, 0.0) else None,
        ask=Decimal(str(ask_raw)) if ask_raw not in (None, 0, 0.0) else None,
        quote_ts=datetime.now(UTC).isoformat(),
    )


@tool(
    "get_quote",
    (
        "Fetch the latest bid/ask/last quote for a US equity ticker. "
        "Returns a JSON TickerSnapshot with last_price, bid, ask, and quote_ts."
    ),
    {"ticker": str},
)
async def get_quote(args: dict[str, Any]) -> dict[str, Any]:
    """Researcher tool — RES-01 — Alpaca IEX primary, yahooquery fallback.

    The SDK registers this under the fully-qualified name
    ``mcp__gekko__get_quote`` (see docs/sdk-shape.md delta #3).
    """
    ctx = get_tool_context()
    ticker = args["ticker"].upper().strip()

    try:
        if ctx.broker is None:
            msg = "broker not configured; falling back to yahooquery"
            raise BrokerOrderError(msg)
        raw = await ctx.broker.get_quote(ticker)
        snapshot = _build_snapshot_from_broker(ticker, raw)
    except Exception as exc:  # noqa: BLE001 — RES-01 fallback design
        log.warning(
            "research.get_quote.broker_fallback",
            ticker=ticker,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )
        try:
            snapshot = _build_snapshot_from_yahooquery(ticker)
        except Exception as exc2:
            log.error(
                "research.get_quote.both_paths_failed",
                ticker=ticker,
                broker_error=str(exc),
                yahoo_error=str(exc2),
            )
            msg = f"get_quote: broker and yahooquery both failed for {ticker}"
            raise BrokerOrderError(msg) from exc2

    ctx.budget.record_call(tokens=_TOKEN_COST)
    payload_json = snapshot.model_dump_json()
    return {
        "content": [{"type": "text", "text": payload_json}],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("get_quote",)
