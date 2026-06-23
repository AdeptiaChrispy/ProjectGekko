"""Researcher → Decision contract — Plan 01-06 Task 2.

The load-bearing structured Brief the Researcher subagent emits and the
Decision subagent consumes (D-10). Raw tool-call transcripts NEVER flow
between the two — only the Pydantic ``ResearchBrief`` does.

The schema is **forward-compatible by design** per RESEARCH §"Pattern 2":
P4 hardening will add optional fields (``injected_content_flags``,
``source_allowlist_violations``, ``sanitization_applied``, ...) WITHOUT
breaking briefs persisted in P1. The ``model_config = ConfigDict(extra="allow")``
declaration is the load-bearing forward-compatibility mechanism — DO NOT
remove it without a coordinated P4 plan change.

References:
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Code Examples — ResearchBrief"
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-10, D-12
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

# ---------------------------------------------------------------------------
# EvidenceSnippet (D-12 building block; reused by TradeProposal in Task 3)
# ---------------------------------------------------------------------------


#: Allowlist of evidence source types — Plan 01-07's Researcher tools emit
#: these. P4 source-domain allowlist enforcement layers atop this.
EvidenceSourceType = Literal[
    "alpaca_quote",
    "finnhub_news",
    "edgar_filing",
    "web_fetch",
]


class EvidenceSnippet(BaseModel):
    """A single piece of evidence the Researcher gathered.

    P4 hardening note: ``quote_text`` is the ONLY field that can contain
    externally-sourced text. The Decision agent's prompt template MUST wrap
    ``quote_text`` in ``<UNTRUSTED>...</UNTRUSTED>`` markers when serializing
    the brief into the Decision agent's context (see RESEARCH §Pitfall 9 +
    P4 prompt-injection defense plan).

    ``summary`` and ``source_type`` are TRUSTED — the Researcher subagent
    authored them; they're inside our trust boundary.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    source_type: EvidenceSourceType
    source_url: HttpUrl | None = None
    fetched_at: str  # ISO 8601
    summary: str = Field(..., min_length=1, max_length=2000)
    quote_text: str | None = None
    relevance_score: Decimal | None = Field(
        None, ge=Decimal("0"), le=Decimal("1")
    )


# ---------------------------------------------------------------------------
# TickerSnapshot
# ---------------------------------------------------------------------------


class TickerSnapshot(BaseModel):
    """A point-in-time snapshot for one ticker the Researcher examined.

    The Researcher attaches up to 20 of these so the Decision agent sees the
    universe-as-of-that-cycle without re-querying.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    ticker: str = Field(..., min_length=1, max_length=16)
    last_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    quote_ts: str  # ISO 8601

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper().strip()


# ---------------------------------------------------------------------------
# ResearchBrief (D-10 load-bearing Researcher → Decision contract)
# ---------------------------------------------------------------------------


class ResearchBrief(BaseModel):
    """The single document the Decision agent sees.

    No raw tool transcripts pass through — the Researcher subagent emits this
    Pydantic instance and the parent runtime serializes ``model_dump_json()``
    into the Decision subagent's prompt (RESEARCH §Pattern 2). The Decision
    agent's tool list is restricted to ``[propose_trade, propose_no_action]``;
    it cannot reach the original tool transcripts.

    Forward-compatibility (P4 hardening):

    * ``model_config = ConfigDict(extra="allow")`` — unknown fields on
      deserialization are PRESERVED in ``model_extra`` rather than rejected.
      This is what makes the schema additive.
    * ``research_budget_used`` is a plain ``dict[str, Any]`` rather than a
      sub-model so P4 can extend its keys without re-versioning the brief.
    * P4 plans to add ``injected_content_flags`` (list[str]),
      ``source_allowlist_violations`` (list[str]), ``sanitization_applied``
      (bool). DO NOT add these to P1 — they're explicitly P4's scope.

    Schema is additive — DO NOT remove or rename fields without a
    coordinated migration. The persisted ``events.payload_json`` rows from P1
    encode the field names verbatim.
    """

    model_config = ConfigDict(frozen=False, extra="allow")

    strategy_name: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)  # UUID per cycle
    generated_at: str  # ISO 8601
    tickers_examined: list[TickerSnapshot] = Field(
        default_factory=list, max_length=20
    )
    catalysts_observed: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceSnippet] = Field(default_factory=list, max_length=10)
    research_budget_used: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    #: Forward-compat slot (Phase-4 Plan 04-03 / RESEARCH §RQ-6).
    #: The Researcher can self-report suspicious patterns it encountered.
    #: The runtime's SC-2 scanner also populates this at brief-parse time
    #: (currently via the audit chain; this field is available for the
    #: Decision prompt to reference in future waves).
    injected_content_flags: list[str] = Field(default_factory=list)


__all__: tuple[str, ...] = (
    "EvidenceSnippet",
    "EvidenceSourceType",
    "ResearchBrief",
    "TickerSnapshot",
)
