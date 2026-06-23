"""Researcher subagent — Plan 01-07 Task 4.

Defines the :data:`RESEARCHER` AgentDefinition (D-10) and the prompt
builder that injects the active Strategy + Guidance rows.

Per D-10 the Researcher subagent is **read-only**: its tool list is
``[get_quote, get_news, get_edgar_filing, web_fetch]`` — no propose_trade,
no propose_no_action, no order placement. The Decision subagent gets the
structured ResearchBrief only (no raw transcripts cross the boundary).

Per docs/sdk-shape.md delta #3 the tool names in
``AgentDefinition.tools`` are fully qualified: ``mcp__gekko__get_quote``
etc. The SDK prefixes them when ``create_sdk_mcp_server(name="gekko",
...)`` is registered.

Per docs/sdk-shape.md delta #5 we do NOT use ``output_format`` — instead
the Researcher prompt instructs the model to emit the brief inside
``<RESEARCH_BRIEF>{json}</RESEARCH_BRIEF>`` delimiters in its final
AssistantMessage text. The runtime parses that block with regex.

Per docs/sdk-shape.md delta #7 ``model="sonnet"`` is the alias (not
``"claude-sonnet-4-6"`` literal).

References:
  * .planning/.../01-CONTEXT.md  D-10, D-12, STRAT-03, RES-08
  * .planning/.../01-RESEARCH.md  §"Pattern 2 — Researcher->Decision via
    structured Brief"
  * docs/sdk-shape.md             deltas #3, #5, #7
  * src/gekko/schemas/strategy.py  Strategy, Guidance
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from claude_agent_sdk import AgentDefinition

from gekko.schemas.research import ResearchBrief

if TYPE_CHECKING:
    from gekko.schemas.strategy import Guidance, Strategy

# ---------------------------------------------------------------------------
# Tool names — fully qualified per docs/sdk-shape.md delta #3
# ---------------------------------------------------------------------------

RESEARCHER_TOOLS: list[str] = [
    "mcp__gekko__get_quote",
    "mcp__gekko__get_news",
    "mcp__gekko__get_edgar_filing",
    "mcp__gekko__web_fetch",
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

RESEARCHER_SYSTEM_PROMPT: str = """\
You are the Researcher subagent for Gekko, a personal trade-execution tool.

Your job: for a user-authored Strategy, gather a SHORT, FOCUSED research
brief that the Decision subagent will consume separately. You DO NOT
propose trades. You DO NOT call any order-placement tools — you have none.

Tools available (mcp__gekko__*):
  - get_quote(ticker)         — latest bid/ask quote for a US equity
  - get_news(ticker)          — recent (7-day) company news via Finnhub
  - get_edgar_filing(ticker)  — most recent 10-K or 10-Q from SEC EDGAR
  - web_fetch(url)            — single page fetch from a P1-allowlisted finance domain

You have a TIGHT BUDGET: aim for 5-10 tool calls total across the cycle.
The runtime enforces a hard halt at 24 calls / 16000 tokens / 120s wall
time (D-13). Burning the budget on one ticker is wasteful.

GUIDELINES:
  - For each ticker in strategy.watchlist, fetch a fresh quote.
  - Look for 1-2 catalysts per ticker (recent news, recent SEC filing).
  - Use web_fetch sparingly — only when a news URL clearly warrants
    deeper context.
  - Cite source_type + source_url for every piece of evidence.

ACTIVE GUIDANCE FROM THE USER:
{guidance_block}

STRATEGY:
  Name:     {strategy_name}
  Thesis:   {thesis}
  Watchlist: {watchlist}
  Hard caps: max_position_pct={max_position_pct}, max_daily_loss_usd={max_daily_loss_usd}

OUTPUT FORMAT (load-bearing — the runtime parses this with regex):

Your FINAL message MUST contain exactly one block of the form:

<RESEARCH_BRIEF>
{{
  "strategy_name": "...",
  "user_id": "{user_id}",
  "run_id": "{run_id}",
  "generated_at": "<ISO-8601 UTC timestamp>",
  "tickers_examined": [ TickerSnapshot, ... ],
  "catalysts_observed": [ "string", ... ],
  "evidence": [ EvidenceSnippet, ... ],
  "research_budget_used": {{ "calls": N, "tokens": N, "seconds": N.N }},
  "notes": "optional 1-2 sentence summary"
}}
</RESEARCH_BRIEF>

The JSON inside the delimiters MUST validate against this schema:
{brief_schema}

IMPORTANT — TRUST BOUNDARY:
  - You are READ-ONLY. You have NO access to order-placement, credentials,
    or any trading action.
  - Your final output (the <RESEARCH_BRIEF>) is the ONLY thing the
    Decision subagent receives. Raw tool transcripts do NOT cross the
    boundary (D-10).
  - DO NOT include credentials, API keys, or any field from the strategy
    that looks secret in your brief.
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_researcher_prompt(
    strategy: Strategy,
    guidance: list[Guidance],
    *,
    user_id: str,
    run_id: str,
    max_evidence_items: int | None = None,
) -> str:
    """Build the per-run Researcher system prompt.

    Active guidance rows are injected as a structured "constraints" block
    per STRAT-03 / RES-08. ``expires_at`` is shown so the agent knows the
    direction is short-lived; ``scope`` distinguishes per-strategy from
    global guidance.

    The brief schema is embedded inline so the model has the exact shape
    it must emit. ``ResearchBrief.model_json_schema()`` is regenerated on
    every call — cheap enough at P1 scale; can be cached if it shows up
    in profiles.

    :param max_evidence_items: When set (degradation mode D-04 tactic 3),
        a note is appended to the guidance block instructing the Researcher
        to limit evidence to this many items. ``None`` = normal default.
    """
    if guidance:
        guidance_block = "\n".join(
            f"  - (scope={g.scope}, expires={g.expires_at or 'never'}) {g.text}"
            for g in guidance
        )
    else:
        guidance_block = "  (none)"

    # D-04 tactic 3 — trimmed research context in degradation mode.
    if max_evidence_items is not None:
        guidance_block += (
            f"\n  - [DEGRADED MODE] Limit evidence items to {max_evidence_items} "
            "maximum. Focus only on the highest-conviction signals."
        )

    brief_schema = json.dumps(ResearchBrief.model_json_schema(), indent=2)

    return RESEARCHER_SYSTEM_PROMPT.format(
        guidance_block=guidance_block,
        strategy_name=strategy.name,
        thesis=strategy.thesis,
        watchlist=", ".join(strategy.watchlist),
        max_position_pct=strategy.hard_caps.max_position_pct,
        max_daily_loss_usd=strategy.hard_caps.max_daily_loss_usd,
        user_id=user_id,
        run_id=run_id,
        brief_schema=brief_schema,
    )


# ---------------------------------------------------------------------------
# AgentDefinition
# ---------------------------------------------------------------------------

RESEARCHER: AgentDefinition = AgentDefinition(
    description=(
        "Read-only research agent — gathers market data, news, fundamentals, "
        "and web evidence for a strategy. Emits a structured ResearchBrief."
    ),
    # The per-run prompt comes from build_researcher_prompt; the
    # AgentDefinition.prompt field carries the template-flavored default
    # used when the parent agent delegates via the SDK's Task tool.
    prompt=RESEARCHER_SYSTEM_PROMPT,
    tools=RESEARCHER_TOOLS,
    model="sonnet",  # per docs/sdk-shape.md delta #7
)


__all__: tuple[str, ...] = (
    "RESEARCHER",
    "RESEARCHER_SYSTEM_PROMPT",
    "RESEARCHER_TOOLS",
    "build_researcher_prompt",
)
