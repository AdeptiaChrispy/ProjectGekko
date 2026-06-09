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
