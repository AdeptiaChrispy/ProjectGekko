"""Researcher tool ``<untrusted_content>`` wrap tests — RES-07 / D-39 — Plan 02-04 Task 2.

Three trust tiers per D-39:

* **Structured-API** (Alpaca quotes, EDGAR filings) — NOT wrapped; we
  parse + pass through as Python dicts / Decimal values.
* **News APIs** (Finnhub) — WRAPPED in
  ``<untrusted_content source="finnhub_news">...</untrusted_content>``.
* **Web** (web_fetch) — host-allowlist-filtered THEN WRAPPED in
  ``<untrusted_content source="web:{host}">...</untrusted_content>``.

These tests cover the WRAP boundary; the host-allowlist gate is covered
by ``tests/unit/test_web_allowlist.py``.

Single-source-of-truth check at the bottom: ``ALLOWED_DOMAINS`` in
``web_fetch`` is an alias for ``WEB_ALLOWLIST`` in
``gekko.research.allowlist`` (no duplicate hardcoded list).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from gekko.agent.budget import BudgetTracker
from gekko.agent.tools.context import set_tool_context
from gekko.schemas.research import EvidenceSnippet


@pytest.fixture
def budget() -> BudgetTracker:
    """Fresh BudgetTracker for each test (avoids cross-test budget leakage)."""
    return BudgetTracker()


# ---------------------------------------------------------------------------
# web_fetch wrap (D-39 Web tier — host allowlist + wrap)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_fetch_wraps_body_in_untrusted_content(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 1: web_fetch return wraps the body in <untrusted_content source="web:{host}">."""
    from gekko.agent.tools import web_fetch as wf_mod
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    get_settings.cache_clear()

    set_tool_context(budget=budget, broker=None)

    # Mock the httpx client so we don't hit the network.
    class _FakeResponse:
        text = "Apple beat earnings expectations in Q3 2026."

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(wf_mod.httpx, "AsyncClient", _FakeClient)

    result = await wf_mod.web_fetch.handler({"url": "https://www.reuters.com/foo"})
    text_block = result["content"][0]["text"]
    snippet = EvidenceSnippet.model_validate_json(text_block)
    assert snippet.quote_text is not None
    assert snippet.quote_text.startswith('<untrusted_content source="web:www.reuters.com">')
    assert snippet.quote_text.endswith("</untrusted_content>")
    assert "Apple beat earnings expectations" in snippet.quote_text


@pytest.mark.asyncio
async def test_web_fetch_uses_lowercased_host_in_wrap(
    budget: BudgetTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior 2: the wrap source uses the lowercased URL hostname."""
    from gekko.agent.tools import web_fetch as wf_mod
    from gekko.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    get_settings.cache_clear()

    set_tool_context(budget=budget, broker=None)

    class _Resp:
        text = "Body content"

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            return _Resp()

    monkeypatch.setattr(wf_mod.httpx, "AsyncClient", _Client)

    # Mixed-case host in URL — wrap source should normalize to lowercase.
    result = await wf_mod.web_fetch.handler({"url": "https://WWW.SEC.GOV/Archives/foo"})
    snippet = EvidenceSnippet.model_validate_json(result["content"][0]["text"])
    assert snippet.quote_text is not None
    assert '<untrusted_content source="web:www.sec.gov">' in snippet.quote_text


@pytest.mark.asyncio
async def test_web_fetch_off_allowlist_rejected_before_wrap(
    budget: BudgetTracker,
) -> None:
    """Behavior 3: off-allowlist hosts are REJECTED before reaching the wrap.

    The Phase-1 host-allowlist gate fires first (raises ValueError); the
    wrap code never executes. Asserts no budget call records — proves
    we did not even reach the network step.
    """
    from gekko.agent.tools.web_fetch import web_fetch

    set_tool_context(budget=budget, broker=None)
    with pytest.raises(ValueError, match="not in P1 allowlist"):
        await web_fetch.handler({"url": "https://malicious.example.com/foo"})
    assert budget.calls == 0


# ---------------------------------------------------------------------------
# finnhub_news wrap (D-39 News tier — wrap article body, NOT headline)
# ---------------------------------------------------------------------------


def test_finnhub_news_wraps_article_body() -> None:
    """Behavior 4: finnhub_news evidence wraps article body in <untrusted_content source="finnhub_news">."""
    from gekko.agent.tools.finnhub_news import _build_evidence_from_row

    row = {
        "headline": "NVDA upgraded by Jefferies",
        "summary": "Jefferies raised its NVDA price target citing AI demand.",
        "url": "https://finnhub.io/news/foo",
        "datetime": 1700000000,
        "source": "finnhub",
    }
    snippet = _build_evidence_from_row(row)
    assert snippet.quote_text is not None
    assert snippet.quote_text.startswith('<untrusted_content source="finnhub_news">')
    assert snippet.quote_text.endswith("</untrusted_content>")
    assert "Jefferies raised its NVDA price target" in snippet.quote_text


def test_finnhub_news_headline_NOT_wrapped() -> None:
    """Behavior 5: headline is Researcher-authored editorial — NOT wrapped.

    Only the article ``summary`` body (third-party news prose) gets the
    ``<untrusted_content>`` wrap. The ``headline`` flows through to
    ``EvidenceSnippet.summary`` un-wrapped per D-39 "wrap article body,
    not headline".
    """
    from gekko.agent.tools.finnhub_news import _build_evidence_from_row

    row = {
        "headline": "NVDA upgraded by Jefferies",
        "summary": "Body text",
        "url": "https://finnhub.io/news/foo",
        "datetime": 1700000000,
    }
    snippet = _build_evidence_from_row(row)
    # EvidenceSnippet.summary should be the bare headline — no wrap markup.
    assert snippet.summary == "NVDA upgraded by Jefferies"
    assert "<untrusted_content" not in snippet.summary


def test_finnhub_news_empty_body_leaves_quote_text_none() -> None:
    """Behavior 6: when Finnhub returns no body, quote_text is None (no wrap)."""
    from gekko.agent.tools.finnhub_news import _build_evidence_from_row

    row = {
        "headline": "Headline only",
        "summary": "",  # empty body
        "url": "https://finnhub.io/news/foo",
        "datetime": 1700000000,
    }
    snippet = _build_evidence_from_row(row)
    assert snippet.quote_text is None


def test_finnhub_wrap_source_is_literal_string() -> None:
    """Behavior 7: wrap source is the literal "finnhub_news" — no host parsing.

    Finnhub is a structured news API; the source is the API name, not
    an arbitrary host. Confirms D-39 News tier wraps with the gateway
    name, not the URL host.
    """
    from gekko.agent.tools.finnhub_news import _build_evidence_from_row

    row = {
        "headline": "h",
        "summary": "body",
        "url": "https://example.com/article",  # irrelevant URL
        "datetime": 1700000000,
    }
    snippet = _build_evidence_from_row(row)
    assert snippet.quote_text is not None
    assert 'source="finnhub_news"' in snippet.quote_text
    # NOT 'source="web:..."' or 'source="example.com"' — gateway name only.
    assert "example.com" not in snippet.quote_text


# ---------------------------------------------------------------------------
# Structured-API tools NOT wrapped (D-39 Structured-API tier)
# ---------------------------------------------------------------------------


def test_alpaca_quote_evidence_NOT_wrapped() -> None:
    """Behavior 8: Alpaca quote tool flows structured data — NO wrap.

    The ``get_quote`` tool returns a ``TickerSnapshot`` (structured
    Decimal price + bid/ask), NOT an EvidenceSnippet with a free-form
    ``quote_text`` field. There is no untrusted-content surface to wrap.
    Trust tier: Structured-API. Confirms the tool source bytes never
    introduce ``<untrusted_content>``.
    """
    import inspect

    from gekko.agent.tools import alpaca_data

    src = inspect.getsource(alpaca_data)
    assert "<untrusted_content" not in src, (
        "alpaca_data tool source must NOT wrap — Structured-API trust tier"
    )


def test_edgar_filing_evidence_NOT_wrapped() -> None:
    """Behavior 9: EDGAR filing tool's EvidenceSnippet has no untrusted_content wrap.

    SEC EDGAR is a trusted government source; the filing summary is
    Researcher-authored editorial (form type + filing date + accession
    + canned one-liner). No third-party prose flows through. Confirms
    the tool source bytes never introduce ``<untrusted_content>``.
    """
    import inspect

    from gekko.agent.tools import edgar

    src = inspect.getsource(edgar)
    assert "<untrusted_content" not in src, (
        "edgar tool source must NOT wrap — Structured-API trust tier"
    )


# ---------------------------------------------------------------------------
# Single-source-of-truth: ALLOWED_DOMAINS is an alias for WEB_ALLOWLIST
# ---------------------------------------------------------------------------


def test_web_fetch_imports_from_allowlist_module() -> None:
    """Behavior 10: web_fetch.py source imports from gekko.research.allowlist."""
    import inspect

    from gekko.agent.tools import web_fetch as wf

    src = inspect.getsource(wf)
    assert "from gekko.research.allowlist import" in src, (
        "web_fetch must import the allowlist from the canonical module "
        "(no duplicate hardcoded ALLOWED_DOMAINS frozenset literal)"
    )


def test_web_fetch_ALLOWED_DOMAINS_is_WEB_ALLOWLIST_alias() -> None:
    """Behavior 11: web_fetch.ALLOWED_DOMAINS IS gekko.research.allowlist.WEB_ALLOWLIST.

    `is` identity test — proves the re-export is an alias, not a copy.
    A copy would silently drift over time as either side mutates; the
    alias means changing one changes both.
    """
    import gekko.agent.tools.web_fetch as wf
    import gekko.research.allowlist as al

    assert wf.ALLOWED_DOMAINS is al.WEB_ALLOWLIST


def test_no_hardcoded_ALLOWED_DOMAINS_literal_in_web_fetch() -> None:
    """Behavior 12: web_fetch.py source does NOT contain a hardcoded frozenset literal.

    Catches a regression where someone re-introduces the inline
    domain list. The string ``ALLOWED_DOMAINS = frozenset`` must not
    appear in the source bytes — only the alias assignment to
    ``WEB_ALLOWLIST``.
    """
    import inspect

    from gekko.agent.tools import web_fetch as wf

    src = inspect.getsource(wf)
    assert "ALLOWED_DOMAINS = frozenset(" not in src, (
        "Hardcoded ALLOWED_DOMAINS frozenset literal must not be reintroduced; "
        "the alias 'ALLOWED_DOMAINS = WEB_ALLOWLIST' is the only assignment allowed."
    )
