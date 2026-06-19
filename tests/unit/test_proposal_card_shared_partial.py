"""Snapshot test — Jinja _proposal_card.html.j2 parity with build_proposal_card.

Plan 03-05 Task 2 (D-55 mirror contract).

Verifies that the Jinja-rendered dashboard card for a given proposal row
contains the same key identifiers (ticker, side, qty, rationale) that
build_proposal_card renders into the Slack card. Both transports must
expose the same core data fields so the operator sees consistent info
regardless of which surface they're on.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader


def _make_test_proposal():
    """Build a TradeProposal + evidence list for snapshot comparison."""
    from datetime import UTC, datetime
    from gekko.schemas.proposal import TradeProposal, AlternativeConsidered
    from gekko.schemas.research import EvidenceSnippet

    now_iso = datetime.now(UTC).isoformat()
    snippets = [
        EvidenceSnippet(
            summary="Strong Q4 earnings beat",
            source_type="finnhub_news",
            fetched_at=now_iso,
        ),
        EvidenceSnippet(
            summary="Analyst upgrades",
            source_type="web_fetch",
            fetched_at=now_iso,
        ),
        EvidenceSnippet(
            summary="Product pipeline strong",
            source_type="edgar_filing",
            fetched_at=now_iso,
        ),
    ]

    alts = [
        AlternativeConsidered(
            description="MSFT position",
            why_rejected="lower margin profile vs AAPL",
        )
    ]

    return TradeProposal(
        ticker="AAPL",
        side="buy",
        qty=Decimal("25"),
        order_type="market",
        rationale="Apple shows strong fundamentals and upcoming product launches",
        evidence=snippets,
        alternatives_considered=alts,
        confidence=Decimal("0.8"),
        decision_id="abc123-decision-id",
        strategy_name="tech-bull",
        user_id="testuser",
        client_order_id="a" * 32,
        account_mode="PAPER",
        target_notional_usd=Decimal("5000"),
    )


def test_proposal_card_shared_partial_schema() -> None:
    """Jinja-rendered card contains the same key fields as build_proposal_card."""
    from gekko.reporter.slack import build_proposal_card

    proposal = _make_test_proposal()
    slack_blocks = build_proposal_card(proposal, account_mode="PAPER")

    # Flatten Slack blocks to search for key field values
    slack_text = str(slack_blocks)
    assert proposal.ticker in slack_text, "ticker must appear in Slack card"
    assert str(proposal.qty) in slack_text, "qty must appear in Slack card"

    # Now render the Jinja template
    templates_dir = (
        Path(__file__).parent.parent.parent
        / "src" / "gekko" / "dashboard" / "templates"
    )
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )

    # Build a proposal-like context dict from the TradeProposal
    # (mirrors what the route handler passes to the template)
    evidence_list = [
        {
            "summary": e.summary,
            "url": str(e.source_url) if e.source_url else "#",
            "source_type": e.source_type,
        }
        for e in proposal.evidence
    ]

    ctx = {
        "proposal_id": proposal.decision_id,
        "ticker": proposal.ticker,
        "side": str(proposal.side).upper(),
        "qty": str(proposal.qty),
        "rationale": proposal.rationale,
        "evidence": evidence_list,
        "status": "PENDING",
        "account_mode": proposal.account_mode,
        "expires_at": None,
        "slack_team_id": "",
        "slack_channel_id": "",
        "timeout_minutes": 30,
        "expired_at_local": "",
    }

    try:
        tmpl = env.get_template("_proposal_card.html.j2")
    except Exception as exc:
        pytest.fail(
            f"_proposal_card.html.j2 template not found: {exc}. "
            "Task 2 requires creating this template."
        )

    html = tmpl.render(**ctx)

    # Parity check: the Jinja card MUST contain the same identifiers
    assert proposal.ticker in html, f"ticker {proposal.ticker!r} must appear in Jinja card"
    assert str(proposal.qty) in html, f"qty {proposal.qty} must appear in Jinja card"
    # Rationale is a user-visible string — at minimum the first 20 chars must appear
    assert proposal.rationale[:20] in html, "rationale must appear in Jinja card"

    # HTMX action attributes (hx-post for approve/reject)
    assert "hx-post" in html, "proposal card must have HTMX hx-post actions"
    assert "approve" in html.lower(), "approve button must be present for PENDING card"
    assert "reject" in html.lower(), "reject button must be present for PENDING card"


def test_approvals_index_unpacks_proposal_dict_into_cards() -> None:
    """DASH-04 regression: the /approvals index must render populated cards.

    The index loops `{% for proposal in proposals %}` and includes the shared
    partial, which reads FLAT context vars (proposal_id, ticker, ...). If the
    index doesn't unpack each proposal dict into those names, every card renders
    blank (data-proposal-id="", empty ticker) — which reads as "no proposals" to
    the operator and breaks the Slack-down fallback. This renders the real index
    with a route-shaped proposal dict and asserts the card is NOT blank.
    """
    templates_dir = (
        Path(__file__).parent.parent.parent
        / "src" / "gekko" / "dashboard" / "templates"
    )
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )

    # Mirrors _build_proposal_ctx() output shape (dashboard/routes.py).
    proposal_ctx = {
        "proposal_id": "deadbeef-pid-0001",
        "ticker": "NVDA",
        "side": "BUY",
        "qty": "2",
        "rationale": "AI infra tailwinds remain strong into Q3",
        "evidence": [],
        "status": "PENDING",
        "account_mode": "PAPER",
        "expires_at": None,
        "expired_at_local": "",
        "timeout_minutes": 30,
        "slack_team_id": "",
        "slack_channel_id": "",
    }

    tmpl = env.get_template("approvals_index.html.j2")
    # No request → base.html.j2 banner blocks are guarded and skipped.
    html = tmpl.render(proposals=[proposal_ctx], request=None, user_id="testuser")

    # The card must carry the real proposal_id (was "" before the unpack fix).
    assert 'data-proposal-id="deadbeef-pid-0001"' in html, (
        "index must unpack proposal.proposal_id into the card (DASH-04 blank-card regression)"
    )
    assert "NVDA" in html, "ticker must render in the index card"
    assert "/approvals/deadbeef-pid-0001/approve" in html, (
        "PENDING card must wire the approve action with the real proposal_id"
    )
    # The empty-state must NOT appear when proposals are present.
    assert "agent is waiting for the next research cycle" not in html
