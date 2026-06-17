"""OrderGuard rejection Block Kit card — Plan 02-05 Task 3 (UI-SPEC §4a).

Covers the ``build_orderguard_rejection_card(reject_code, reject_reason,
ticker, strategy_name, proposal_id)`` builder:

* Shape: list[dict] matching UI-SPEC §4a (header → fields section → explainer)
* Header glyph + label per UI-SPEC §"Banner & Section Headers"
* Every interpolated value routes through ``_escape_mrkdwn`` (defense in depth)
* Card is renderable as JSON (no non-serializable bytes)
"""

from __future__ import annotations

import json
import re

import pytest

from gekko.reporter.slack import build_orderguard_rejection_card


def test_rejection_card_shape_matches_ui_spec() -> None:
    """Card is list[dict] with header → section(fields) → section(explainer)."""
    blocks = build_orderguard_rejection_card(
        reject_code="universe",
        reject_reason="AAPL not in strategy watchlist [TSLA, NVDA]",
        ticker="AAPL",
        strategy_name="ai-infra-bull",
        proposal_id="prop-abc123",
    )
    assert isinstance(blocks, list)
    assert len(blocks) == 3
    assert blocks[0]["type"] == "header"
    assert blocks[1]["type"] == "section"
    assert "fields" in blocks[1]
    assert blocks[2]["type"] == "section"
    assert "text" in blocks[2]


def test_rejection_card_header_text_per_ui_spec() -> None:
    """Header text matches UI-SPEC §"Banner & Section Headers"."""
    blocks = build_orderguard_rejection_card(
        reject_code="universe",
        reject_reason="test",
        ticker="AAPL",
        strategy_name="s",
        proposal_id="p",
    )
    header_text = blocks[0]["text"]["text"]
    assert "REJECTED BY ORDERGUARD" in header_text
    assert "🔴" in header_text


def test_rejection_card_fields_carry_all_5_values() -> None:
    """Field section carries reject_code + reason + ticker + strategy + proposal."""
    blocks = build_orderguard_rejection_card(
        reject_code="hard_cap_position_pct",
        reject_reason="Position $10000 exceeds 5% of $1000 equity",
        ticker="NVDA",
        strategy_name="my-strategy",
        proposal_id="prop-99",
    )
    fields = blocks[1]["fields"]
    joined = " | ".join(f["text"] for f in fields)
    # The mrkdwn escape backslash-escapes `_`, so check the escaped form.
    assert "hard\\_cap\\_position\\_pct" in joined
    assert "Position" in joined
    assert "NVDA" in joined
    assert "my-strategy" in joined
    assert "prop-99" in joined


def test_rejection_card_explainer_text_per_ui_spec() -> None:
    """Explainer section quotes "deterministic Python firewall" per UI-SPEC §4a."""
    blocks = build_orderguard_rejection_card(
        reject_code="universe",
        reject_reason="test",
        ticker="A",
        strategy_name="s",
        proposal_id="p",
    )
    explainer = blocks[2]["text"]["text"]
    assert "deterministic Python firewall" in explainer
    assert "No order was sent" in explainer


def test_rejection_card_escapes_mrkdwn_metacharacters() -> None:
    """Interpolated reject_reason with mrkdwn metacharacters is escaped.

    Defense-in-depth: even though reject_reasons are deterministic Python
    strings, the same render path applies per UI-SPEC §"Slack Block Kit
    Parallels Summary" consistency lock.
    """
    blocks = build_orderguard_rejection_card(
        reject_code="universe",
        reject_reason="ticker `RM*K` not in watchlist <safe>",
        ticker="A",
        strategy_name="s",
        proposal_id="p",
    )
    fields_text = " | ".join(f["text"] for f in blocks[1]["fields"])
    # Backslash-escaped: ` * < > etc.
    assert "\\`" in fields_text
    assert "\\*" in fields_text
    assert "\\<" in fields_text
    assert "\\>" in fields_text


def test_rejection_card_is_json_serializable() -> None:
    """The card payload survives a JSON round-trip (Slack acceptance gate)."""
    blocks = build_orderguard_rejection_card(
        reject_code="kill_active",
        reject_reason="Kill switch is ON",
        ticker="NVDA",
        strategy_name="ai-infra-bull",
        proposal_id="prop-xyz",
    )
    json_str = json.dumps(blocks)
    assert "REJECTED BY ORDERGUARD" in json_str
    # `_` is mrkdwn-escaped → "kill\\_active" survives the JSON round-trip
    # as "kill\\\\_active" in the dumped string.
    assert "kill\\\\_active" in json_str or "kill\\_active" in json_str
    # Round-trip parses cleanly back to dict.
    parsed = json.loads(json_str)
    assert parsed == blocks


def test_constants_exported() -> None:
    """Reporter templates exports the new Phase-2 constants."""
    from gekko.reporter.templates import (
        KILL_ACTIVE_BANNER,
        KILL_ACTIVE_BANNER_BOOT_RESTORED,
        LIVE_BANNER_STRONG,
        ORDERGUARD_REJECTION_HEADER,
    )

    assert "KILL ACTIVE" in KILL_ACTIVE_BANNER
    assert "restored" in KILL_ACTIVE_BANNER_BOOT_RESTORED.lower()
    assert "REAL MONEY" in LIVE_BANNER_STRONG
    assert "REJECTED BY ORDERGUARD" in ORDERGUARD_REJECTION_HEADER
