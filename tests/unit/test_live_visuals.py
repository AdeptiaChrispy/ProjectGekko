"""Live-mode dashboard visuals + Slack first-live card — Plan 02-06 Task 3.

Tests:
  * Live banner template renders the expected red header copy + ARIA.
  * [LIVE] chip appears on the strategies-list row when live_mode_eligible.
  * Promote-to-Live button appears only on paper strategies.
  * first_live_confirm.html.j2 has NO inline <script> (CSP-clean).
  * build_first_live_card produces the Block Kit shape per UI-SPEC §3a.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from gekko.reporter.slack import build_first_live_card
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

_TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / ".."
    / "src"
    / "gekko"
    / "dashboard"
    / "templates"
).resolve()


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
    )


# ---------------------------------------------------------------------------
# Template render tests
# ---------------------------------------------------------------------------


def test_live_banner_template_renders_expected_copy_and_aria() -> None:
    tpl = _env().get_template("live_banner.html.j2")
    out = tpl.render()
    assert "[LIVE — REAL MONEY]" in out
    assert "Alpaca live trading is armed" in out
    # ARIA role + aria-live for accessibility.
    assert 'role="alert"' in out
    assert 'aria-live="polite"' in out
    assert 'aria-atomic="true"' in out
    assert "banner-live-strong" in out


def test_strategies_list_chip_live_renders_only_on_live_eligible() -> None:
    tpl = _env().get_template("strategies_list.html.j2")
    # Mode-1: live-eligible strategy — chip should appear.
    out_live = tpl.render(
        user_id="test-user",
        strategies=[
            {
                "strategy_name": "ai-infra",
                "version": 1,
                "watchlist_preview": "NVDA, AMD",
                "mode": "live",
                "live_mode_eligible": True,
            }
        ],
        # request and other context not needed for the chip portion.
        request=None,
    )
    assert 'class="chip-live"' in out_live
    assert ">LIVE<" in out_live
    # Promote-to-Live button suppressed.
    assert "Promote to Live" not in out_live

    # Mode-2: paper-only strategy — chip absent, Promote button visible.
    out_paper = tpl.render(
        user_id="test-user",
        strategies=[
            {
                "strategy_name": "paper-only",
                "version": 1,
                "watchlist_preview": "NVDA",
                "mode": "paper",
                "live_mode_eligible": False,
            }
        ],
        request=None,
    )
    assert 'class="chip-live"' not in out_paper
    assert "Promote to Live" in out_paper


import re as _re


def _strip_jinja_comments(s: str) -> str:
    """Remove Jinja {# ... #} comment blocks before SRI lint."""
    return _re.sub(r"\{#.*?#\}", "", s, flags=_re.DOTALL)


def test_first_live_confirm_has_no_inline_script() -> None:
    """CSP `script-src 'self'` requires no inline <script>. PATTERNS §4 row 7."""
    body = _strip_jinja_comments(
        (_TEMPLATES / "first_live_confirm.html.j2").read_text(encoding="utf-8")
    )
    # No literal <script in the body.
    assert "<script" not in body.lower()
    # No inline onclick / onsubmit either.
    assert "onclick=" not in body.lower()
    assert "onsubmit=" not in body.lower()


def test_live_confirm_success_has_no_inline_script() -> None:
    body = _strip_jinja_comments(
        (_TEMPLATES / "live_confirm_success.html.j2").read_text(
            encoding="utf-8"
        )
    )
    assert "<script" not in body.lower()
    assert "onclick=" not in body.lower()


def test_live_banner_has_no_inline_script() -> None:
    body = _strip_jinja_comments(
        (_TEMPLATES / "live_banner.html.j2").read_text(encoding="utf-8")
    )
    assert "<script" not in body.lower()


def test_promote_modal_has_no_inline_script() -> None:
    body = _strip_jinja_comments(
        (_TEMPLATES / "promote_to_live_modal.html.j2").read_text(
            encoding="utf-8"
        )
    )
    assert "<script" not in body.lower()
    assert "onclick=" not in body.lower()


# ---------------------------------------------------------------------------
# Slack build_first_live_card
# ---------------------------------------------------------------------------


def _make_live_proposal() -> TradeProposal:
    return TradeProposal(
        user_id="test-user",
        strategy_name="first-live",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        target_notional_usd=Decimal("500"),
        order_type="limit",
        limit_price=Decimal("100"),
        rationale="bullish on AI infra",
        confidence=Decimal("0.7"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/q/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="$100",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/n/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="news",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(description="AMD", why_rejected="lower"),
        ],
        client_order_id="a" * 32,
        account_mode="LIVE",
    )


def test_build_first_live_card_has_expected_blocks() -> None:
    tp = _make_live_proposal()
    blocks = build_first_live_card(tp, dashboard_url="http://localhost:8000")

    # Header
    header = next((b for b in blocks if b.get("type") == "header"), None)
    assert header is not None
    assert "FIRST LIVE TRADE" in header["text"]["text"]
    assert "DUAL CONFIRM" in header["text"]["text"]

    # Action button has the URL pointing at /live-confirm/{decision_id}
    action_block = next(
        (b for b in blocks if b.get("type") == "actions"), None
    )
    assert action_block is not None
    button = action_block["elements"][0]
    assert button["type"] == "button"
    assert button["url"] == (
        f"http://localhost:8000/live-confirm/{tp.decision_id}"
    )
    assert button["text"]["text"] == "Open Dashboard to Confirm"
    # NO action_id — URL buttons don't round-trip.
    assert "action_id" not in button

    # NO inline Approve/Reject buttons — only ONE action element.
    assert len(action_block["elements"]) == 1

    # Warning text present.
    rendered_text = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    assert "FIRST live trade" in rendered_text
    assert "second-channel" in rendered_text


def test_build_first_live_card_escapes_llm_authored_text() -> None:
    """The rationale field (LLM-authored) must be mrkdwn-escaped."""
    tp = _make_live_proposal()
    # Inject metacharacters into a free-form field.
    tp = tp.model_copy(
        update={"rationale": "spoof *bold* and `code`"}
    )
    blocks = build_first_live_card(tp, dashboard_url="http://x")
    rendered = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    # Backslash-escaped mrkdwn metacharacters.
    assert "\\*bold\\*" in rendered
    assert "\\`code\\`" in rendered
