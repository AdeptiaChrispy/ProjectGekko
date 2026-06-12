"""Tests for ``gekko.reporter.slack`` — Plan 01-08 Task 1.

Per the plan's ``<behavior>`` block, 14 behaviors covering Block Kit shape +
HITL-01 field completeness + REG-01 disclosure + edge cases (market order /
None best-effort fields / no_action / fill confirmation).

The tests do NOT invoke a real Slack workspace — every assertion is on the
returned Block Kit list/string. The shape is validated by:

1. Top-level type checks (every block has the expected ``type`` discriminator).
2. Substring search through the JSON-serialized payload — Slack's Block Kit
   format is well-documented but extensive; substring search is a load-
   bearing simple check that's robust to ordering / formatting drift.
3. Action-button ``action_id`` checks — these are the IDs that
   ``@slack_app.action(...)`` handlers match against in Task 3.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from gekko.reporter.slack import (
    _truncate_for_slack,
    build_fill_confirmation,
    build_no_action_message,
    build_proposal_card,
)
from gekko.reporter.templates import REG_01_DISCLOSURE, UNKNOWN_FIELD_PLACEHOLDER
from gekko.schemas.proposal import (
    AlternativeConsidered,
    NoActionProposal,
    TradeProposal,
)
from gekko.schemas.research import EvidenceSnippet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_evidence() -> list[EvidenceSnippet]:
    return [
        EvidenceSnippet(
            source_type="alpaca_quote",
            source_url="https://alpaca.markets/quotes/NVDA",
            fetched_at="2026-06-08T12:00:00+00:00",
            summary="NVDA last trade $1,234.56 (bid 1,234.40 / ask 1,234.70)",
            relevance_score=Decimal("0.85"),
        ),
        EvidenceSnippet(
            source_type="finnhub_news",
            source_url="https://finnhub.io/news/nvda-earnings",
            fetched_at="2026-06-08T11:30:00+00:00",
            summary="NVDA Q1 earnings beat consensus by 12%.",
        ),
        EvidenceSnippet(
            source_type="edgar_filing",
            source_url="https://www.sec.gov/Archives/edgar/data/0001045810/000104581026000034/",
            fetched_at="2026-06-08T10:00:00+00:00",
            summary="10-Q filed 2026-06-01 — data center revenue up 56% YoY.",
        ),
    ]


def _sample_trade_proposal(
    *,
    ticker: str = "NVDA",
    side: str = "buy",
    order_type: str = "limit",
    limit_price: str | None = "1234.56",
) -> TradeProposal:
    return TradeProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        ticker=ticker,
        side=side,
        qty=Decimal("5"),
        order_type=order_type,
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        stop_price=None,
        rationale=(
            "Earnings beat + analyst upgrade aligns with thesis on AI "
            "infrastructure leaders."
        ),
        confidence=Decimal("0.78"),
        evidence=_sample_evidence(),
        alternatives_considered=[
            AlternativeConsidered(
                description="Consider AMD as the cheaper alternative",
                why_rejected="Lower confidence on data-center exposure vs. NVDA",
            ),
            AlternativeConsidered(
                description="Hold and wait for a pullback to 1180",
                why_rejected="Momentum is intact; pullback may not arrive",
            ),
        ],
        client_order_id="a" * 32,
    )


# ---------------------------------------------------------------------------
# Behaviors 1-13 — build_proposal_card
# ---------------------------------------------------------------------------


def test_build_proposal_card_returns_list_of_block_dicts() -> None:
    """build_proposal_card returns a non-empty list[dict]."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    assert isinstance(blocks, list)
    assert len(blocks) > 0
    for block in blocks:
        assert isinstance(block, dict)
        assert "type" in block


def test_first_block_is_header_with_paper_banner() -> None:
    """Account mode PAPER renders a green PAPER banner in the first header block."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p, account_mode="PAPER")
    assert blocks[0]["type"] == "header"
    header_text = blocks[0]["text"]["text"]
    assert "PAPER" in header_text
    # The green-circle indicator MUST be present per HITL-01 paper/live indicator
    assert "🟢" in header_text


def test_live_banner_when_account_mode_live() -> None:
    """Account mode LIVE renders the red LIVE banner (forward-compat with P2)."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p, account_mode="LIVE")
    header_text = blocks[0]["text"]["text"]
    assert "LIVE" in header_text
    assert "🔴" in header_text


def test_card_includes_all_required_hitl_01_fields() -> None:
    """The card contains ticker, side (uppercased), qty, order_type, limit_price,
    confidence, strategy_name — the HITL-01 required field set."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    payload = json.dumps(blocks)
    assert "NVDA" in payload
    assert "BUY" in payload  # side uppercased
    assert "5" in payload  # qty
    assert "limit" in payload or "LIMIT" in payload  # order_type
    assert "1234.56" in payload  # limit_price
    assert "0.78" in payload  # confidence
    assert "ai-infra-bull" in payload  # strategy_name


def test_company_name_and_sector_render_when_provided() -> None:
    """Populated company_name + sector render as labeled mrkdwn fields per HITL-01."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(
        p,
        account_mode="PAPER",
        company_name="NVIDIA Corp",
        sector="Technology",
    )
    payload = json.dumps(blocks)
    assert "NVIDIA Corp" in payload
    assert "Technology" in payload


def test_company_name_and_sector_render_unknown_when_none() -> None:
    """None best-effort fields render UNKNOWN_FIELD_PLACEHOLDER per HITL-01.

    The card shape stays stable — we don't drop the field row; we render
    the italicized placeholder so the user knows the data was unavailable.
    """
    p = _sample_trade_proposal()
    blocks_none = build_proposal_card(
        p, account_mode="PAPER", company_name=None, sector=None
    )
    payload_none = json.dumps(blocks_none)
    # Placeholder appears exactly where company / sector would otherwise be
    assert UNKNOWN_FIELD_PLACEHOLDER in payload_none

    # And the card's shape (number of blocks) matches the populated case
    blocks_populated = build_proposal_card(
        p,
        account_mode="PAPER",
        company_name="NVIDIA Corp",
        sector="Technology",
    )
    assert len(blocks_none) == len(blocks_populated)


def test_card_includes_rationale_section() -> None:
    """Rationale text appears in a section.text.mrkdwn block."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    payload = json.dumps(blocks)
    assert "Earnings beat" in payload  # from rationale
    assert "Rationale" in payload  # the label


def test_card_renders_evidence_with_links() -> None:
    """Each evidence snippet renders as a markdown link bullet with source_url + source_type."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    payload = json.dumps(blocks)
    # Slack mrkdwn link form: <url|label>
    assert "<https://alpaca.markets/quotes/NVDA|alpaca_quote>" in payload
    assert "<https://finnhub.io/news/nvda-earnings|finnhub_news>" in payload
    assert "Q1 earnings beat" in payload  # the summary text


def test_card_renders_alternatives_considered() -> None:
    """Each alternative renders with description and italicized why_rejected."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    payload = json.dumps(blocks)
    assert "Consider AMD" in payload
    # Why_rejected wrapped in underscores (mrkdwn italic) per the plan's `_why_`
    assert "_Lower confidence on data-center exposure vs. NVDA_" in payload


def test_card_includes_approve_and_reject_buttons_with_decision_id() -> None:
    """Approve + Reject action buttons exist with value=decision_id, primary/danger style."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    # Locate the action block
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) >= 1
    actions = action_blocks[0]["elements"]
    by_action_id = {a["action_id"]: a for a in actions}

    assert "approve_proposal" in by_action_id
    assert by_action_id["approve_proposal"]["value"] == p.decision_id
    assert by_action_id["approve_proposal"]["style"] == "primary"

    assert "reject_proposal" in by_action_id
    assert by_action_id["reject_proposal"]["value"] == p.decision_id
    assert by_action_id["reject_proposal"]["style"] == "danger"


def test_card_includes_edit_size_and_escalate_stub_buttons() -> None:
    """Edit Size + Escalate buttons exist (P3 stubs) with proper action_ids."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) >= 1
    actions = action_blocks[0]["elements"]
    by_action_id = {a["action_id"]: a for a in actions}

    assert "edit_size" in by_action_id
    assert "escalate_to_dashboard" in by_action_id
    # All four buttons carry the same decision_id value (handlers re-load the
    # row from DB; the value just routes the action)
    assert by_action_id["edit_size"]["value"] == p.decision_id
    assert by_action_id["escalate_to_dashboard"]["value"] == p.decision_id


def test_card_includes_reg_01_compliance_footer() -> None:
    """A trailing context block with the REG-01 disclosure text is present."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    # Find the last context block
    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert len(context_blocks) >= 1
    # REG-01 wording is the canonical disclosure string
    rendered = json.dumps(context_blocks)
    assert "Not investment advice" in rendered
    assert REG_01_DISCLOSURE in rendered


def test_card_json_serializes() -> None:
    """The complete card payload survives json.dumps without TypeError."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    # If this raises, the card carries a non-JSON-serializable type
    payload = json.dumps(blocks)
    # Sanity: parse it back
    parsed = json.loads(payload)
    assert isinstance(parsed, list)


def test_market_order_renders_mkt_for_price() -> None:
    """A market order (limit_price=None) renders the price field as 'mkt'."""
    p = _sample_trade_proposal(order_type="market", limit_price=None)
    blocks = build_proposal_card(p)
    payload = json.dumps(blocks)
    # 'mkt' is the canonical placeholder per RESEARCH §Code Examples
    assert "mkt" in payload


# ---------------------------------------------------------------------------
# build_no_action_message
# ---------------------------------------------------------------------------


def test_build_no_action_message_carries_strategy_and_rationale() -> None:
    """build_no_action_message returns a verbose D-09 string with strategy +
    rationale + cost summary."""
    nap = NoActionProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        rationale="NVDA price too elevated vs thesis.",
        factors_considered=["price_vs_thesis", "macro_risk"],
        confidence=Decimal("0.60"),
    )
    msg = build_no_action_message(nap, cost_usd=Decimal("0.12"))
    assert "ai-infra-bull" in msg
    assert "NVDA price too elevated" in msg
    assert "no action" in msg.lower()
    # Cost line
    assert "0.12" in msg


def test_build_no_action_message_without_cost() -> None:
    """cost_usd=None is OK; cost line is omitted."""
    nap = NoActionProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        rationale="Macro headwinds dominate.",
        factors_considered=["fed_policy"],
        confidence=Decimal("0.55"),
    )
    msg = build_no_action_message(nap, cost_usd=None)
    assert "ai-infra-bull" in msg
    assert "Macro headwinds" in msg


# ---------------------------------------------------------------------------
# build_fill_confirmation
# ---------------------------------------------------------------------------


def test_mrkdwn_metacharacters_in_llm_fields_are_escaped() -> None:
    """Prompt-injected mrkdwn in LLM-authored fields must not spoof card structure.

    A malicious ``rationale`` / ``summary`` / ``why_rejected`` containing
    newlines + mrkdwn metacharacters (``< > * _ ~ | ``) must be escaped so it
    renders as inert text, not as new fields, bold headings, or fake links.
    """
    nasty = "FAKE\n*Approved by Chris*: <https://evil.test|click> _|_"
    p = TradeProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        order_type="limit",
        limit_price=Decimal("1234.56"),
        stop_price=None,
        rationale=nasty,
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary=nasty,
            ),
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-08T12:00:00+00:00",
                summary="benign summary",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://www.sec.gov/Archives/edgar/data/0001045810/x/",
                fetched_at="2026-06-08T10:00:00+00:00",
                summary="another benign summary",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(description=nasty, why_rejected=nasty),
        ],
        client_order_id="a" * 32,
    )
    blocks = build_proposal_card(p)
    # Walk the raw Python strings (not the JSON-encoded payload — JSON adds
    # its own backslash-escaping layer that would obscure the assertions).
    # Only "section" blocks built with a single text body have b["text"]; the
    # primary-fields section uses "fields" instead, so guard with a presence check.
    def _section_text(label: str) -> str:
        for b in blocks:
            if b.get("type") != "section":
                continue
            text_obj = b.get("text")
            if isinstance(text_obj, dict) and label in text_obj.get("text", ""):
                return text_obj["text"]
        raise AssertionError(f"section with label {label!r} not found")

    rationale_block_text = _section_text("Rationale:")
    evidence_block_text = _section_text("Evidence:")
    alternatives_block_text = _section_text("Alternatives considered:")

    for text in (rationale_block_text, evidence_block_text, alternatives_block_text):
        # Newlines in LLM content are collapsed to a single space — multiline
        # content can't break out of its row.
        assert "FAKE\n" not in text
        # Mrkdwn metacharacters in LLM-authored text are backslash-escaped.
        # The literal "*Approved by Chris*" (no backslashes) must not appear
        # in any block carrying LLM content.
        assert "*Approved by Chris*" not in text
        assert "\\*Approved by Chris\\*" in text
    # The malicious link form is neutered: < > | are all escaped so Slack
    # renders inert text, not a clickable link.
    assert "<https://evil.test|click>" not in rationale_block_text
    assert "\\<https://evil.test\\|click\\>" in rationale_block_text
    # The legitimate evidence link (built by us, not from LLM content) is intact.
    assert "<https://alpaca.markets/quotes/NVDA|alpaca_quote>" in evidence_block_text


def test_no_action_message_escapes_mrkdwn_metacharacters() -> None:
    """``build_no_action_message`` applies the same escaping to LLM-authored
    rationale, strategy name, and factor labels."""
    nap = NoActionProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        rationale="thin evidence\n*BUY NOW* <https://evil.test|click>",
        factors_considered=["price_vs_*thesis*", "macro_risk"],
        confidence=Decimal("0.55"),
    )
    msg = build_no_action_message(nap, cost_usd=Decimal("0.05"))
    # Newlines collapsed; metacharacters (including underscores in factor
    # labels) backslash-escaped.
    assert "\n*BUY NOW*" not in msg
    assert "\\*BUY NOW\\*" in msg
    assert "<https://evil.test|click>" not in msg
    # Underscores are mrkdwn italic markers and get escaped along with the *s.
    assert "price\\_vs\\_\\*thesis\\*" in msg


def test_build_fill_confirmation_returns_short_dm_text() -> None:
    """build_fill_confirmation returns a short single-line message with all required fields."""
    msg = build_fill_confirmation(
        client_order_id="a" * 32,
        broker_order_id="paper-broker-order-abc123",
        filled_qty=Decimal("5"),
        filled_avg_price=Decimal("1234.56"),
        ticker="NVDA",
        strategy_name="ai-infra-bull",
        side="buy",
    )
    assert "filled" in msg.lower()
    assert "NVDA" in msg
    assert "5" in msg
    assert "1234.56" in msg
    assert "ai-infra-bull" in msg
    assert "BUY" in msg


# ---------------------------------------------------------------------------
# Slack 3000-char section-block truncation guard (quick 260612-dix)
# ---------------------------------------------------------------------------
#
# Plan 01-09 Task 5 walking-skeleton demo exposed two failure modes:
# (a) Sonnet routinely emits ~1200-3500-char rationales (Pydantic schema cap
#     raised to 5000 in the companion fix).
# (b) Slack section.text is hard-capped at 3000 chars — a 5000-char rationale
#     would trip ``invalid_blocks``.
# _truncate_for_slack defends (b) BEFORE _escape_mrkdwn (escape can expand
# length). 2900 + ~40-char marker leaves ample headroom under 3000.

_TRUNCATION_MARKER = "…[truncated; see audit log for full text]"


def test_truncate_for_slack_short_text_unchanged() -> None:
    """Short text under the limit passes through unmodified."""
    assert _truncate_for_slack("hello world") == "hello world"


def test_truncate_for_slack_at_boundary_unchanged() -> None:
    """Text at exactly the 2900-char default limit is not truncated."""
    text = "x" * 2900
    assert _truncate_for_slack(text) == text


def test_truncate_for_slack_over_boundary_truncates() -> None:
    """One char over the limit yields ``2900 'x's + truncation marker``.

    The marker ``"…[truncated; see audit log for full text]"`` is 41 chars
    (the leading horizontal-ellipsis U+2026 counts as one Python char), so
    the total truncated length is 2900 + 41 = 2941, still well under
    Slack's 3000-char section.text ceiling.
    """
    text = "x" * 2901
    result = _truncate_for_slack(text)
    assert result.startswith("x" * 2900)
    assert result.endswith(_TRUNCATION_MARKER)
    # 2900 raw + 41-char marker = 2941 total, well under Slack's 3000 ceiling.
    assert len(result) == 2900 + len(_TRUNCATION_MARKER)
    assert len(result) < 3000


def test_card_rationale_truncated_when_long() -> None:
    """A 4500-char rationale in the proposal card is truncated to ≤ 3000 chars
    in the Rationale section block, with the visible marker present."""
    long_rationale = "A" * 4500
    p = TradeProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        order_type="limit",
        limit_price=Decimal("1234.56"),
        stop_price=None,
        rationale=long_rationale,
        confidence=Decimal("0.78"),
        evidence=_sample_evidence(),
        alternatives_considered=[
            AlternativeConsidered(
                description="Consider AMD as the cheaper alternative",
                why_rejected="Lower confidence on data-center exposure vs. NVDA",
            ),
        ],
        client_order_id="a" * 32,
    )
    blocks = build_proposal_card(p)
    # Locate the Rationale section block
    rationale_block_text: str | None = None
    for b in blocks:
        if b.get("type") != "section":
            continue
        text_obj = b.get("text")
        if isinstance(text_obj, dict) and "*Rationale:*" in text_obj.get("text", ""):
            rationale_block_text = text_obj["text"]
            break
    assert rationale_block_text is not None, "Rationale section not found"
    assert len(rationale_block_text) <= 3000
    assert _TRUNCATION_MARKER in rationale_block_text


def test_card_rationale_not_truncated_when_short() -> None:
    """The existing ~90-char fixture rationale is NOT truncated — no marker present."""
    p = _sample_trade_proposal()
    blocks = build_proposal_card(p)
    rationale_block_text: str | None = None
    for b in blocks:
        if b.get("type") != "section":
            continue
        text_obj = b.get("text")
        if isinstance(text_obj, dict) and "*Rationale:*" in text_obj.get("text", ""):
            rationale_block_text = text_obj["text"]
            break
    assert rationale_block_text is not None
    assert _TRUNCATION_MARKER not in rationale_block_text


def test_no_action_message_truncates_long_rationale() -> None:
    """build_no_action_message truncates a 4500-char rationale and shows the marker."""
    long_rationale = "B" * 4500
    nap = NoActionProposal(
        user_id="U_TEST",
        strategy_name="ai-infra-bull",
        decision_id=uuid4().hex,
        rationale=long_rationale,
        factors_considered=["price_vs_thesis"],
        confidence=Decimal("0.60"),
    )
    msg = build_no_action_message(nap, cost_usd=Decimal("0.05"))
    assert _TRUNCATION_MARKER in msg
    # The outer D-09 template still renders
    assert "Reviewed" in msg
