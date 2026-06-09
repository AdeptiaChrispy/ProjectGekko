"""Tests for the four Researcher tools — Plan 01-07 Task 3.

Per the plan's <action> block, we cover:

1. get_quote uses the broker when available.
2. get_quote falls back to yahooquery when the broker raises.
3. get_news degrades gracefully when FINNHUB_API_KEY is unset.
4. get_news returns EvidenceSnippet-shaped JSON when finnhub responds.
5. get_edgar_filing sends the User-Agent header per SEC fair-use.
6. web_fetch rejects off-allowlist domains.
7. web_fetch accepts an allowlisted (reuters.com) URL and returns an
   EvidenceSnippet.
8. BudgetTracker.record_call is invoked once per successful tool call.

All tools follow docs/sdk-shape.md deltas #1 and #2 — they take a single
``args: dict`` and return the MCP content shape. Tests pull the
JSON-string out of ``result["content"][0]["text"]`` and re-parse via
the relevant Pydantic schema.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from gekko.agent.budget import BudgetTracker
from gekko.agent.tools.context import clear_tool_context, set_tool_context
from gekko.schemas.research import EvidenceSnippet, TickerSnapshot


@pytest.fixture(autouse=True)
def _reset_tool_context() -> Any:
    """Clear the module-global tool context between tests."""
    clear_tool_context()
    yield
    clear_tool_context()


@pytest.fixture
def budget() -> BudgetTracker:
    return BudgetTracker()


def _extract_text(result: dict[str, Any]) -> str:
    """Pull the JSON text payload out of an MCP content-shape return."""
    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"
    return block["text"]


# ---------------------------------------------------------------------------
# get_quote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quote_uses_broker(budget: BudgetTracker) -> None:
    """Behavior 1: ``get_quote`` calls broker.get_quote and returns TickerSnapshot."""
    from gekko.agent.tools.alpaca_data import get_quote

    broker = MagicMock()
    broker.get_quote = AsyncMock(
        return_value={
            "ask_price": "182.50",
            "bid_price": "182.30",
            "timestamp": "2026-06-09T14:00:00+00:00",
        }
    )
    set_tool_context(budget=budget, broker=broker)

    # The decorated tool is an ``SdkMcpTool``; call its underlying handler.
    result = await get_quote.handler({"ticker": "nvda"})
    text = _extract_text(result)
    snapshot = TickerSnapshot.model_validate_json(text)

    assert snapshot.ticker == "NVDA"
    # mid-quote: (182.50 + 182.30) / 2 = 182.40
    assert snapshot.last_price == Decimal("182.40")
    assert snapshot.bid == Decimal("182.30")
    assert snapshot.ask == Decimal("182.50")
    broker.get_quote.assert_awaited_once_with("NVDA")
    assert budget.calls == 1
    assert budget.tokens_used == 100


@pytest.mark.asyncio
async def test_get_quote_falls_back_to_yahooquery(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 2: broker failure routes to yahooquery."""
    from gekko.agent.tools import alpaca_data
    from gekko.agent.tools.alpaca_data import get_quote

    broker = MagicMock()
    broker.get_quote = AsyncMock(side_effect=RuntimeError("alpaca down"))
    set_tool_context(budget=budget, broker=broker)

    # Stub the yahooquery fallback by monkeypatching the module-level helper.
    fake_snapshot = TickerSnapshot(
        ticker="NVDA",
        last_price=Decimal("182.10"),
        bid=Decimal("182.00"),
        ask=Decimal("182.20"),
        quote_ts="2026-06-09T14:00:00+00:00",
    )

    def _fake_yq(ticker: str) -> TickerSnapshot:
        assert ticker == "NVDA"
        return fake_snapshot

    monkeypatch.setattr(
        alpaca_data, "_build_snapshot_from_yahooquery", _fake_yq
    )

    result = await get_quote.handler({"ticker": "NVDA"})
    text = _extract_text(result)
    snapshot = TickerSnapshot.model_validate_json(text)

    assert snapshot.last_price == Decimal("182.10")
    assert budget.calls == 1


# ---------------------------------------------------------------------------
# get_news
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finnhub_news_degrades_gracefully_without_key(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 3: missing FINNHUB_API_KEY returns [] without raising."""
    from gekko.agent.tools.finnhub_news import get_news
    from gekko.config import get_settings

    # Seed minimal env so Settings constructs; explicitly drop FINNHUB.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    get_settings.cache_clear()

    set_tool_context(budget=budget, broker=None)
    result = await get_news.handler({"ticker": "NVDA"})
    text = _extract_text(result)
    assert json.loads(text) == []
    assert budget.calls == 1
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_finnhub_news_returns_evidence_snippets(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 4: finnhub client output round-trips through EvidenceSnippet."""
    from gekko.agent.tools import finnhub_news
    from gekko.agent.tools.finnhub_news import get_news
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    monkeypatch.setenv("FINNHUB_API_KEY", "fnhb_test")
    get_settings.cache_clear()

    rows = [
        {
            "headline": "NVIDIA unveils new GPU",
            "summary": "Press release: ...",
            "url": "https://reuters.com/business/nvidia-gpu",
            "datetime": 1717977600,
            "source": "Reuters",
        },
        {
            "headline": "Analyst raises NVDA target",
            "summary": "Bullish thesis ...",
            "url": "https://wsj.com/markets/nvda",
            "datetime": 1717891200,
            "source": "WSJ",
        },
    ]

    def fake_sync(api_key: str, ticker: str) -> list[dict[str, Any]]:
        assert api_key == "fnhb_test"
        assert ticker == "NVDA"
        return rows

    monkeypatch.setattr(finnhub_news, "_call_finnhub_sync", fake_sync)

    set_tool_context(budget=budget, broker=None)
    result = await get_news.handler({"ticker": "NVDA"})
    text = _extract_text(result)
    payload = json.loads(text)
    assert isinstance(payload, list)
    assert len(payload) == 2
    for row in payload:
        snippet = EvidenceSnippet.model_validate(row)
        assert snippet.source_type == "finnhub_news"
        assert snippet.summary  # non-empty
    assert budget.calls == 1
    assert budget.tokens_used == 200
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# get_edgar_filing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edgar_sends_user_agent_header(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 5: EDGAR requests carry the User-Agent header from settings."""
    from gekko.agent.tools.edgar import get_edgar_filing
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    monkeypatch.setenv("GEKKO_USER_AGENT", "Gekko/test test@example.com")
    get_settings.cache_clear()

    tickers_payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    }
    submissions_payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "8-K"],
                "filingDate": ["2026-02-15", "2026-01-01"],
                "accessionNumber": ["0001045810-26-000001", "0001045810-26-000002"],
                "primaryDocument": ["nvda-10k.htm", "nvda-8k.htm"],
            }
        }
    }

    with respx.mock(assert_all_called=False) as router:
        tickers_route = router.get(
            "https://www.sec.gov/files/company_tickers.json"
        ).mock(return_value=httpx.Response(200, json=tickers_payload))
        submissions_route = router.get(
            "https://data.sec.gov/submissions/CIK0001045810.json"
        ).mock(return_value=httpx.Response(200, json=submissions_payload))

        set_tool_context(budget=budget, broker=None)
        result = await get_edgar_filing.handler({"ticker": "NVDA"})

        # The User-Agent header MUST match settings.gekko_user_agent.
        ua = tickers_route.calls.last.request.headers.get("User-Agent")
        assert ua == "Gekko/test test@example.com"
        ua2 = submissions_route.calls.last.request.headers.get("User-Agent")
        assert ua2 == "Gekko/test test@example.com"

    text = _extract_text(result)
    snippet = EvidenceSnippet.model_validate_json(text)
    assert snippet.source_type == "edgar_filing"
    assert "10-K" in snippet.summary
    assert "2026-02-15" in snippet.summary
    assert str(snippet.source_url).startswith(
        "https://www.sec.gov/Archives/edgar/data/1045810/"
    )
    assert budget.calls == 1
    assert budget.tokens_used == 300
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_fetch_rejects_off_allowlist_domain(
    budget: BudgetTracker,
) -> None:
    """Behavior 6: off-allowlist host raises ValueError before any network call."""
    from gekko.agent.tools.web_fetch import web_fetch

    set_tool_context(budget=budget, broker=None)
    with pytest.raises(ValueError, match="not in P1 allowlist"):
        await web_fetch.handler({"url": "https://malicious.example.com/foo"})
    # Budget unchanged — we never reached record_call.
    assert budget.calls == 0


@pytest.mark.asyncio
async def test_web_fetch_accepts_reuters(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 7: allowlisted reuters URL returns an EvidenceSnippet."""
    from gekko.agent.tools.web_fetch import web_fetch
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    get_settings.cache_clear()

    with respx.mock(assert_all_called=True) as router:
        router.get("https://reuters.com/business/foo").mock(
            return_value=httpx.Response(
                200,
                text=(
                    "<html><body><h1>Reuters headline</h1>"
                    "Some news body text here.</body></html>"
                ),
            )
        )
        set_tool_context(budget=budget, broker=None)
        result = await web_fetch.handler(
            {"url": "https://reuters.com/business/foo"}
        )

    text = _extract_text(result)
    snippet = EvidenceSnippet.model_validate_json(text)
    assert snippet.source_type == "web_fetch"
    assert str(snippet.source_url) == "https://reuters.com/business/foo"
    assert "Reuters headline" in (snippet.quote_text or "")
    assert budget.calls == 1
    assert budget.tokens_used == 500
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Budget.record_call invariant (behavior 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_record_called_on_every_tool_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavior 8: every successful tool call invokes BudgetTracker.record_call.

    We run get_quote, get_news (degraded path), and web_fetch (allowlisted) —
    after the three invocations, ``budget.calls`` must be exactly 3.
    """
    from gekko.agent.tools import finnhub_news
    from gekko.agent.tools.alpaca_data import get_quote
    from gekko.agent.tools.finnhub_news import get_news
    from gekko.agent.tools.web_fetch import web_fetch
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    get_settings.cache_clear()

    budget = BudgetTracker()
    broker = MagicMock()
    broker.get_quote = AsyncMock(
        return_value={
            "ask_price": "100.10",
            "bid_price": "100.00",
            "timestamp": "2026-06-09T14:00:00+00:00",
        }
    )
    set_tool_context(budget=budget, broker=broker)

    await get_quote.handler({"ticker": "NVDA"})
    await get_news.handler({"ticker": "NVDA"})  # degraded path

    with respx.mock(assert_all_called=True) as router:
        router.get("https://reuters.com/x").mock(
            return_value=httpx.Response(200, text="<html><body>hello</body></html>")
        )
        await web_fetch.handler({"url": "https://reuters.com/x"})

    assert budget.calls == 3
    get_settings.cache_clear()
