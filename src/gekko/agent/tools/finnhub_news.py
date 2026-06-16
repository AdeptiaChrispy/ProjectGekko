"""``get_news`` Researcher tool — RES-02 — Plan 01-07 Task 3.

Wraps the Finnhub free-tier ``company_news`` endpoint. Graceful-degrades to
an empty list when ``settings.finnhub_api_key`` is unset — the Researcher
subagent should still be able to complete a cycle even without news
(RESEARCH §"Environment Availability").

Per docs/sdk-shape.md deltas #1 and #2:

* ``@tool("get_news", "...", {"ticker": str})``
* Function signature ``async def get_news(args: dict) -> dict`` returns
  MCP content shape with a JSON-encoded list of EvidenceSnippet dumps.

The SDK registers this under ``mcp__gekko__get_news``.

References:
  * .planning/.../01-RESEARCH.md  §"Code Examples — finnhub news"
  * src/gekko/schemas/research.py  EvidenceSnippet
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from claude_agent_sdk import tool

from gekko.agent.tools.context import get_tool_context
from gekko.config import get_settings
from gekko.logging_config import get_logger
from gekko.schemas.research import EvidenceSnippet

log = get_logger(__name__)

#: Token-cost estimate per ``get_news`` invocation.
_TOKEN_COST: int = 200

#: How far back to look for company news in days.
_LOOKBACK_DAYS: int = 7

#: Cap on how many news items to return — keep the brief tight.
_MAX_ITEMS: int = 5


def _build_evidence_from_row(row: dict[str, Any]) -> EvidenceSnippet:
    """Coerce a Finnhub news row into an :class:`EvidenceSnippet`.

    Finnhub rows carry ``headline``, ``summary``, ``url``, ``datetime``
    (epoch seconds), and ``source`` keys.

    Per Phase-2 D-39 / RES-07 the article body (the ``summary`` field
    from Finnhub, which is third-party news prose) is wrapped in
    ``<untrusted_content source="finnhub_news">...</untrusted_content>``
    markers BEFORE it reaches the Researcher's tool result. The
    ``headline`` stays unwrapped — it becomes the EvidenceSnippet
    ``summary`` field which is Researcher-authored editorial summary
    per the EvidenceSnippet schema docstring (D-39 News tier: wrap
    article body, not headline).
    """
    headline = (row.get("headline") or "").strip()
    summary_text = (row.get("summary") or "").strip()
    summary = headline if headline else summary_text[:200]
    if not summary:
        summary = "(no headline or summary)"
    url = row.get("url") or None
    epoch = row.get("datetime")
    fetched_at = (
        datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()
        if epoch
        else datetime.now(UTC).isoformat()
    )

    # D-39 / RES-07 Site 1 (News tier): wrap article body in
    # <untrusted_content source="finnhub_news"> markers. If the
    # article has no body text, leave quote_text as None — there's
    # nothing untrusted to mark up.
    if summary_text:
        body_clamped = summary_text[:2000]
        quote_text_wrapped: str | None = (
            f'<untrusted_content source="finnhub_news">\n'
            f"{body_clamped}\n"
            f"</untrusted_content>"
        )
    else:
        quote_text_wrapped = None

    return EvidenceSnippet(
        source_type="finnhub_news",
        source_url=url,
        fetched_at=fetched_at,
        summary=summary[:2000],
        quote_text=quote_text_wrapped,
    )


def _call_finnhub_sync(api_key: str, ticker: str) -> list[dict[str, Any]]:
    """Call the Finnhub client synchronously — wrapped by asyncio.to_thread.

    Imported lazily to keep import-time cost down.
    """
    import finnhub

    client = finnhub.Client(api_key=api_key)
    today = datetime.now(UTC).date()
    start = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    end = today.isoformat()
    rows: list[dict[str, Any]] = client.company_news(ticker, _from=start, to=end) or []
    return rows[:_MAX_ITEMS]


@tool(
    "get_news",
    (
        "Fetch recent (last 7 days) company news for a US equity ticker from "
        "Finnhub. Returns a JSON list of EvidenceSnippet objects."
    ),
    {"ticker": str},
)
async def get_news(args: dict[str, Any]) -> dict[str, Any]:
    """Researcher tool — RES-02 — Finnhub company_news, graceful degrade."""
    ctx = get_tool_context()
    ticker = args["ticker"].upper().strip()
    settings = get_settings()

    if settings.finnhub_api_key is None:
        log.warning(
            "research.get_news.degraded",
            reason="FINNHUB_API_KEY not configured; returning empty list",
            ticker=ticker,
        )
        ctx.budget.record_call(tokens=_TOKEN_COST)
        return {
            "content": [{"type": "text", "text": json.dumps([])}],
            "is_error": False,
        }

    api_key = settings.finnhub_api_key.get_secret_value()
    try:
        rows = await asyncio.to_thread(_call_finnhub_sync, api_key, ticker)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        log.error(
            "research.get_news.error",
            ticker=ticker,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )
        ctx.budget.record_call(tokens=_TOKEN_COST)
        return {
            "content": [{"type": "text", "text": json.dumps([])}],
            "is_error": False,
        }

    evidence = [_build_evidence_from_row(r) for r in rows]
    payload = [e.model_dump(mode="json") for e in evidence]
    ctx.budget.record_call(tokens=_TOKEN_COST)
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("get_news",)
