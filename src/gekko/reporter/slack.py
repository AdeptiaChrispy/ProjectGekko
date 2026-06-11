"""Slack Block Kit reporter — Plan 01-08 Task 1.

Builds the user-facing Block Kit payloads:

* :func:`build_proposal_card` — the full HITL-01 verbose proposal card.
  Header (PAPER/LIVE banner) + fields (ticker / company / sector / side /
  qty / type+price / confidence / strategy) + rationale + evidence
  bullets (mrkdwn links) + alternatives_considered + action buttons
  (Approve / Reject / Edit-Size stub / Escalate stub) + REG-01
  compliance footer.

* :func:`build_no_action_message` — verbose D-09 no-action DM text.

* :func:`build_fill_confirmation` — single-line "paper order filled" DM.

Per RESEARCH §"Don't Hand-Roll" we prefer ``slack-sdk`` typed builders
where convenient — but the card needs custom mrkdwn fields, italicized
``why_rejected``, and a 4-button actions block, so the typed builders are
awkward. We construct dict literals directly and validate by:

1. Type-discriminator presence on every block (``"type"`` key).
2. The JSON-dumps round trip in tests (Slack rejects on missing required
   fields, so substring-search on the rendered payload catches drift).

Field completeness (HITL-01): ``company_name`` and ``sector`` are
best-effort because the broker / data feed may not always surface them.
When ``None`` we render :data:`UNKNOWN_FIELD_PLACEHOLDER` ("_unknown_")
italicized so the user sees the data was unavailable rather than thinking
we forgot the field. The card's block count stays stable across the
populated / ``None`` cases — neither field is conditionally omitted.

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  HITL-01, REG-01,
    D-09 (verbose no_action), D-12 (evidence / alternatives capture)
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Code Examples —
    proposal card builder"
  * .planning/phases/01-foundation.../01-SKELETON.md  §"What's Real vs
    Minimal — Slack row" — edit-size + escalate buttons are P3 stubs
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from gekko.reporter.templates import (
    LIVE_BANNER,
    PAPER_BANNER,
    REG_01_DISCLOSURE,
    UNKNOWN_FIELD_PLACEHOLDER,
)
from gekko.schemas.proposal import NoActionProposal, TradeProposal

# ---------------------------------------------------------------------------
# Block builders — internal helpers
# ---------------------------------------------------------------------------

_MRKDWN_META = re.compile(r"([<>*_~|`])")
_WS_RUN = re.compile(r"\s+")


def _escape_mrkdwn(text: str | None) -> str:
    """Escape Slack mrkdwn metacharacters in LLM- or user-supplied free-form text.

    Prevents prompt-injected content from spoofing card structure: a malicious
    summary like ``"FAKE\\n*Approved by Chris*: confirmed"`` could otherwise
    impersonate a new field row inside an mrkdwn block. We backslash-escape
    ``< > * _ ~ | `` ` `` per Slack's mrkdwn escape rules and collapse any
    whitespace runs (including newlines) to a single space so multiline
    content can't break out of its row.

    Trusted fields (``HttpUrl``, ``Literal``, ``Decimal``, schema-validated
    ids/tickers) are NOT routed through this — only free-form text the LLM
    or user can author.
    """
    if text is None:
        return ""
    collapsed = _WS_RUN.sub(" ", str(text)).strip()
    return _MRKDWN_META.sub(r"\\\1", collapsed)


def _banner(account_mode: str) -> str:
    """Return the colored banner string for the header block.

    P1 always passes ``"PAPER"`` (D-24); ``"LIVE"`` is the forward-compat
    branch for P2's OrderGuard promotion.
    """
    if account_mode.upper() == "PAPER":
        return PAPER_BANNER
    return LIVE_BANNER


def _price_field(proposal: TradeProposal) -> str:
    """Render the order-type + price string per HITL-01.

    Market orders show ``"mkt"`` as the price; limit / stop orders show
    the configured limit_price. Stop orders carry a stop_price separately
    but the HITL-01 card surface treats the price field as the
    "execution-anchor" price the user evaluates the proposal against.
    """
    order_type = str(proposal.order_type)
    if proposal.limit_price is None and proposal.stop_price is None:
        return f"{order_type} @ mkt"
    price = proposal.limit_price if proposal.limit_price is not None else proposal.stop_price
    return f"{order_type} @ {price}"


def _evidence_mrkdwn(proposal: TradeProposal) -> str:
    """Render the evidence bullets as ``• <url|source_type>: summary`` lines.

    Slack mrkdwn link form is ``<url|label>``; ``source_url`` can be None
    on free-form evidence (rare), so we degrade gracefully to the
    source_type label without a link.
    """
    lines: list[str] = []
    for e in proposal.evidence:
        # source_type is a schema Literal and source_url is HttpUrl — both
        # structurally validated, no escape needed. e.summary is LLM-authored
        # free-form text → escape mrkdwn metacharacters.
        summary = _escape_mrkdwn(e.summary)
        if e.source_url is not None:
            # str() on HttpUrl gives the canonical URL form
            lines.append(f"• <{e.source_url!s}|{e.source_type}>: {summary}")
        else:
            lines.append(f"• _{e.source_type}_: {summary}")
    return "\n".join(lines)


def _alternatives_mrkdwn(proposal: TradeProposal) -> str:
    """Render alternatives as ``• description — _why_rejected_`` lines.

    The italicized ``_why_rejected_`` is the mrkdwn "_..._" form; we wrap
    the value tightly so Slack renders the italics correctly even when
    the value itself contains punctuation.
    """
    lines: list[str] = []
    for a in proposal.alternatives_considered:
        # Both fields are LLM-authored free-form text.
        description = _escape_mrkdwn(a.description)
        why_rejected = _escape_mrkdwn(a.why_rejected)
        lines.append(f"• {description} — _{why_rejected}_")
    return "\n".join(lines)


def _field_value_or_unknown(value: str | None) -> str:
    """Best-effort field rendering — value or UNKNOWN_FIELD_PLACEHOLDER.

    HITL-01 field completeness: card shape stays stable across present /
    absent best-effort fields (company_name, sector).
    """
    return value if value is not None else UNKNOWN_FIELD_PLACEHOLDER


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_proposal_card(
    proposal: TradeProposal,
    account_mode: str = "PAPER",
    *,
    company_name: str | None = None,
    sector: str | None = None,
) -> list[dict[str, Any]]:
    """Render the verbose HITL-01 trade-proposal Block Kit card.

    :param proposal: The validated TradeProposal from the Decision agent
        (Plan 01-07 ProposalWriter).
    :param account_mode: ``"PAPER"`` (P1 default) or ``"LIVE"`` (P2 forward-
        compat). Determines the colored banner in the header block.
    :param company_name: Best-effort company name (e.g., "NVIDIA Corp").
        Pass ``None`` when the broker / data feed did not supply one — the
        card renders ``_unknown_`` so the field row stays in place.
    :param sector: Best-effort sector (e.g., "Technology"). Same
        ``None`` semantics as ``company_name``.
    :returns: A list of Block Kit block dicts ready to pass to
        ``slack_client.chat_postMessage(blocks=...)``.
    """
    banner = _banner(account_mode)
    side_upper = str(proposal.side).upper()
    # Best-effort fields come from broker / data feed strings — escape as
    # defense-in-depth since the placeholder branch returns trusted constants.
    company_display = (
        _escape_mrkdwn(company_name) if company_name is not None else UNKNOWN_FIELD_PLACEHOLDER
    )
    sector_display = (
        _escape_mrkdwn(sector) if sector is not None else UNKNOWN_FIELD_PLACEHOLDER
    )
    price_display = _price_field(proposal)
    evidence_md = _evidence_mrkdwn(proposal)
    alternatives_md = _alternatives_mrkdwn(proposal)
    rationale_md = _escape_mrkdwn(proposal.rationale)
    strategy_md = _escape_mrkdwn(proposal.strategy_name)
    decision_id_value = proposal.decision_id

    return [
        # 1. Header — colored banner per account_mode
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{banner} — Trade Proposal",
                "emoji": True,
            },
        },
        # 2. Primary fields — ticker / company / sector / side / qty / type+price
        #    / confidence / strategy. 8 mrkdwn cells = HITL-01 field set.
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ticker:* {proposal.ticker}"},
                {"type": "mrkdwn", "text": f"*Company:* {company_display}"},
                {"type": "mrkdwn", "text": f"*Sector:* {sector_display}"},
                {"type": "mrkdwn", "text": f"*Side:* {side_upper}"},
                {"type": "mrkdwn", "text": f"*Qty:* {proposal.qty}"},
                {"type": "mrkdwn", "text": f"*Type:* {price_display}"},
                {"type": "mrkdwn", "text": f"*Confidence:* {proposal.confidence}"},
                {"type": "mrkdwn", "text": f"*Strategy:* {strategy_md}"},
            ],
        },
        # 3. Rationale
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Rationale:* {rationale_md}",
            },
        },
        # 4. Evidence (3-5 bullets with mrkdwn links)
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Evidence:*\n{evidence_md}",
            },
        },
        # 5. Alternatives considered (1+ bullets with italicized why_rejected)
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Alternatives considered:*\n{alternatives_md}",
            },
        },
        # 6. Action buttons — Approve / Reject (primary) + Edit Size /
        #    Escalate (P3 stubs). All four carry value=decision_id.
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "value": decision_id_value,
                    "action_id": "approve_proposal",
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "value": decision_id_value,
                    "action_id": "reject_proposal",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit Size"},
                    "value": decision_id_value,
                    "action_id": "edit_size",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Escalate"},
                    "value": decision_id_value,
                    "action_id": "escalate_to_dashboard",
                },
            ],
        },
        # 7. REG-01 compliance footer
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": REG_01_DISCLOSURE},
            ],
        },
    ]


def build_no_action_message(
    no_action: NoActionProposal, cost_usd: Decimal | None = None
) -> str:
    """Render the verbose D-09 no-action Slack DM text.

    Example output (matching CONTEXT.md D-09):
        "Reviewed ai-infra-bull, no action — NVDA price too elevated vs
        thesis. Factors considered: price_vs_thesis, macro_risk. Spent ~$0.12."

    :param no_action: The validated NoActionProposal from the Decision
        agent.
    :param cost_usd: Optional per-cycle research cost. Omitted from the
        message when ``None``.
    """
    # Strategy name, rationale, and factor labels are user / LLM-authored —
    # escape mrkdwn metacharacters so injected content can't impersonate
    # additional message rows.
    strategy_safe = _escape_mrkdwn(no_action.strategy_name)
    rationale_safe = _escape_mrkdwn(no_action.rationale)
    factors_line = ""
    if no_action.factors_considered:
        joined = ", ".join(_escape_mrkdwn(f) for f in no_action.factors_considered)
        factors_line = f" Factors considered: {joined}."
    cost_line = f" Spent ~${cost_usd}." if cost_usd is not None else ""
    return (
        f"Reviewed {strategy_safe}, no action — "
        f"{rationale_safe}.{factors_line}{cost_line} "
        f"{REG_01_DISCLOSURE}"
    )


def build_fill_confirmation(
    *,
    client_order_id: str,
    broker_order_id: str,
    filled_qty: Decimal,
    filled_avg_price: Decimal,
    ticker: str,
    strategy_name: str,
    side: str,
) -> str:
    """Render the post-fill confirmation DM text.

    Single line per SKELETON §Demo Script — the user is meant to see this
    arrive seconds after clicking Approve.

    Example output (matching the SKELETON Demo Script):
        "Paper order filled: BUY 5 NVDA @ $1,234.56 — strategy=ai-infra-bull"

    :param client_order_id: Deterministic id from the Proposal row (D-20).
    :param broker_order_id: Broker-side primary key (Alpaca order id).
    :param filled_qty: Decimal — the quantity actually filled.
    :param filled_avg_price: Decimal — VWAP across the fill events.
    :param ticker: Symbol.
    :param strategy_name: Strategy slug for traceability.
    :param side: 'buy' / 'sell' — rendered uppercased.
    """
    side_upper = side.upper()
    return (
        f"Paper order filled: {side_upper} {filled_qty} {ticker} "
        f"@ ${filled_avg_price} — strategy={strategy_name} "
        f"(client_order_id={client_order_id[:8]}…, "
        f"broker_order_id={broker_order_id})"
    )


__all__: tuple[str, ...] = (
    "build_fill_confirmation",
    "build_no_action_message",
    "build_proposal_card",
)
