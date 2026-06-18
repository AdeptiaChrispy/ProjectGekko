"""Tests for build_proposal_card expired=True branch — Plan 03-04 Task 2.

Tests:
(a) assembled Block Kit payload contains the [EXPIRED] chip in the header section
(b) NO ``actions`` block is present in the expired card
(c) context block contains the expected status string with expired_at + timeout_minutes
(d) LIVE banner is preserved when account_mode="LIVE" + expired=True
(e) mock for slack_app.client.chat_update verifies channel + ts + blocks are passed
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from gekko.reporter.slack import build_proposal_card
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

# ---------------------------------------------------------------------------
# Shared test proposal
# ---------------------------------------------------------------------------


def _make_tp(**overrides: Any) -> TradeProposal:
    defaults: dict[str, Any] = {
        "decision_id": uuid4().hex,
        "user_id": "test-user",
        "strategy_name": "ai-infra-bull",
        "ticker": "NVDA",
        "side": "buy",
        "qty": Decimal("5"),
        "order_type": "market",
        "limit_price": None,
        "stop_price": None,
        "confidence": Decimal("0.85"),
        "rationale": "Strong AI demand.",
        "evidence": [
            EvidenceSnippet(source_type="alpaca_quote", fetched_at="2026-06-18T10:00:00+00:00", summary="NVDA quote at $900"),
            EvidenceSnippet(source_type="finnhub_news", fetched_at="2026-06-18T09:00:00+00:00", summary="Earnings beat consensus"),
            EvidenceSnippet(source_type="edgar_filing", fetched_at="2026-06-18T08:00:00+00:00", summary="10-Q revenue up 56% YoY"),
        ],
        "alternatives_considered": [
            AlternativeConsidered(description="AMD alternative", why_rejected="Lower margin profile"),
        ],
        "client_order_id": "a" * 32,
        "target_notional_usd": Decimal("1000"),
        "account_mode": "PAPER",
        "wash_sale_flag": None,
    }
    defaults.update(overrides)
    return TradeProposal(**defaults)


# ---------------------------------------------------------------------------
# (a) [EXPIRED] chip in header section
# ---------------------------------------------------------------------------


def test_expired_chip_in_header_section() -> None:
    """Assembled Block Kit payload contains [EXPIRED] chip in the first section."""
    tp = _make_tp()
    blocks = build_proposal_card(tp, expired=True, expired_at_local="10:00 UTC", timeout_minutes=30)

    # Find a section block containing [EXPIRED]
    serialized = json.dumps(blocks)
    assert "[EXPIRED]" in serialized, f"[EXPIRED] chip not found in blocks: {serialized[:500]}"

    # Specifically, one of the early section blocks should carry [EXPIRED] in the text.
    section_texts = [
        b.get("text", {}).get("text", "")
        for b in blocks
        if b.get("type") == "section"
    ]
    assert any("[EXPIRED]" in t for t in section_texts), (
        f"No section block contains [EXPIRED]. Section texts: {section_texts}"
    )


# ---------------------------------------------------------------------------
# (b) NO actions block in expired card
# ---------------------------------------------------------------------------


def test_no_actions_block_in_expired_card() -> None:
    """The actions block (Approve / Reject / Edit Size buttons) must NOT be present."""
    tp = _make_tp()
    blocks = build_proposal_card(tp, expired=True, expired_at_local="10:00 UTC", timeout_minutes=30)

    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 0, (
        f"Expired card must have NO actions block. Found: {action_blocks}"
    )


# ---------------------------------------------------------------------------
# (c) Context block with expected status string
# ---------------------------------------------------------------------------


def test_context_block_contains_expired_status_string() -> None:
    """Context block carries the expired status text per UI-SPEC §Surface 4."""
    tp = _make_tp()
    expired_at = "10:00 UTC"
    timeout_minutes = 45
    blocks = build_proposal_card(tp, expired=True, expired_at_local=expired_at, timeout_minutes=timeout_minutes)

    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert len(context_blocks) >= 1, "No context block found in expired card"

    # The expiry-specific context block should contain both expired_at and timeout_minutes.
    all_context_text = " ".join(
        elem.get("text", "")
        for b in context_blocks
        for elem in b.get("elements", [])
    )
    assert expired_at in all_context_text, (
        f"expired_at {expired_at!r} not found in context blocks: {all_context_text}"
    )
    assert str(timeout_minutes) in all_context_text, (
        f"timeout_minutes {timeout_minutes} not found in context blocks: {all_context_text}"
    )


# ---------------------------------------------------------------------------
# (d) LIVE banner preserved with expired=True
# ---------------------------------------------------------------------------


def test_live_banner_preserved_when_expired() -> None:
    """The LIVE banner must appear even when expired=True (P2-locked visual)."""
    tp = _make_tp()
    blocks = build_proposal_card(tp, "LIVE", expired=True, expired_at_local="10:00 UTC", timeout_minutes=30)

    serialized = json.dumps(blocks)
    assert "LIVE" in serialized, f"LIVE banner not present in expired LIVE card: {serialized[:500]}"

    # The header block should contain the LIVE banner.
    header_blocks = [b for b in blocks if b.get("type") == "header"]
    assert len(header_blocks) >= 1
    header_text = header_blocks[0].get("text", {}).get("text", "")
    assert "LIVE" in header_text, f"LIVE not in header text: {header_text!r}"


# ---------------------------------------------------------------------------
# (e) chat_update called with correct channel, ts, blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_update_expired_card_called_with_correct_args() -> None:
    """_chat_update_expired_card calls slack_app.client.chat_update with correct args."""
    from gekko.approval.expiry import _chat_update_expired_card

    # Build a mock proposal row with slack coordinates and payload.
    tp = _make_tp()
    mock_row = MagicMock()
    mock_row.proposal_id = tp.decision_id
    mock_row.slack_message_ts = "1234567890.000100"
    mock_row.slack_message_channel = "D_TEST_CHAN"
    mock_row.payload_json = tp.model_dump_json()
    mock_row.account_mode = "PAPER"
    mock_row._strategy_payload_json = "{}"

    mock_chat_update = AsyncMock()
    mock_slack_client = MagicMock()
    mock_slack_client.chat_update = mock_chat_update

    with (
        patch("gekko.reporter.slack.build_proposal_card", return_value=[{"type": "header", "text": {"type": "plain_text", "text": "[EXPIRED] test"}}]),
        patch("gekko.slack.app.slack_app") as mock_slack_app,
    ):
        mock_slack_app.client.chat_update = mock_chat_update
        await _chat_update_expired_card(mock_row)

    mock_chat_update.assert_called_once()
    call_kwargs = mock_chat_update.call_args.kwargs
    assert call_kwargs["channel"] == "D_TEST_CHAN"
    assert call_kwargs["ts"] == "1234567890.000100"
    assert "blocks" in call_kwargs


# ---------------------------------------------------------------------------
# (f) Missing slack_message_ts gracefully skips chat_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_update_skipped_when_missing_ts() -> None:
    """When slack_message_ts is None, _chat_update_expired_card is a no-op."""
    from gekko.approval.expiry import _chat_update_expired_card

    mock_row = MagicMock()
    mock_row.proposal_id = "prop-no-ts"
    mock_row.slack_message_ts = None
    mock_row.slack_message_channel = "D_TEST_CHAN"
    mock_row.payload_json = "{}"
    mock_row.account_mode = "PAPER"

    mock_chat_update = AsyncMock()

    with patch("gekko.slack.app.slack_app") as mock_slack_app:
        mock_slack_app.client.chat_update = mock_chat_update
        await _chat_update_expired_card(mock_row)

    mock_chat_update.assert_not_called()
