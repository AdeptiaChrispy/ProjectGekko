"""TradeProposal + NoActionProposal contracts — Plan 01-06 Task 3.

The Decision agent's structured output. ``no_action`` is first-class (D-11)
so the Decision agent cannot fall back to free-form prose when it has nothing
to propose — it MUST emit one of these two shapes via Pydantic-validated tool
use.

D-12 invariants (the one-shot architectural decision per CONTEXT.md — cannot
be retrofitted from free-form prose):

* TradeProposal carries ``min_length=3 max_length=5`` evidence snippets
* TradeProposal carries ``min_length=1`` ``alternatives_considered`` items
* TradeProposal carries ``confidence`` Decimal in [0, 1]
* NoActionProposal carries ``min_length=1`` ``factors_considered`` items
* NoActionProposal carries ``confidence`` Decimal in [0, 1]

D-15 says the audit log's ``payload_json`` IS the ``model_dump()`` of these
proposals — so the schema constraints are the rationale-capture invariant for
the v2 retrospective dashboard.

References:
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Code Examples — TradeProposal"
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-11, D-12, D-14, D-15
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gekko.core.types import OrderSide, OrderType
from gekko.schemas.research import EvidenceSnippet

# ---------------------------------------------------------------------------
# AlternativeConsidered
# ---------------------------------------------------------------------------


class AlternativeConsidered(BaseModel):
    """One alternative the Decision agent considered and rejected (D-12)."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    description: str = Field(..., min_length=1, max_length=500)
    why_rejected: str = Field(..., min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# TradeProposal (D-11, D-12)
# ---------------------------------------------------------------------------


class TradeProposal(BaseModel):
    """A structured trade proposal from the Decision agent (D-11, D-12).

    The 3-5 evidence + 1+ alternatives_considered + confidence triple is the
    structured-rationale differentiator that makes v2's retrospective
    dashboard possible. Once persisted, the ``payload_json`` of the
    corresponding ``proposal`` event in the audit log IS this dump.

    ``client_order_id`` is computed by the Proposal Writer (Plan 01-07) using
    :func:`gekko.core.ids.compute_client_order_id` and persisted on the row
    BEFORE any broker call — this is the deterministic idempotency key that
    blocks duplicate orders (Knight Capital prevention, EXEC-02 / D-20).

    ``extra="ignore"`` keeps forward-compatibility: if a P4 / P6 / Pn schema
    revision adds an optional field and we deserialize an older proposal row,
    the unknown future field is dropped silently rather than rejected.

    Order-type / price-field coupling (model_validator):

    * ``order_type == "limit"`` requires ``limit_price is not None``
    * ``order_type == "stop"`` requires ``stop_price is not None``
    * ``order_type == "market"`` is permissive: ``limit_price`` and
      ``stop_price`` may be present but are informational only — the
      Executor (Plan 01-08) routes by ``order_type``, not by which price
      fields are set. We do NOT strip the price fields on market orders
      because keeping the Decision agent's intent visible in the audit log
      is more valuable than enforcing a strict null.
    """

    model_config = ConfigDict(frozen=False, extra="ignore")

    user_id: str = Field(..., min_length=1)
    strategy_name: str = Field(..., min_length=1)
    decision_id: str = Field(..., min_length=1)
    ticker: str = Field(..., min_length=1, max_length=16)
    side: OrderSide
    qty: Decimal = Field(..., gt=Decimal("0"))
    order_type: OrderType = OrderType.LIMIT
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    rationale: str = Field(..., min_length=1, max_length=5000)
    confidence: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    evidence: list[EvidenceSnippet] = Field(..., min_length=3, max_length=5)
    alternatives_considered: list[AlternativeConsidered] = Field(
        ..., min_length=1
    )
    client_order_id: str = Field(..., min_length=32, max_length=32)

    @model_validator(mode="after")
    def _validate_price_for_order_type(self) -> TradeProposal:
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            msg = "limit orders require limit_price"
            raise ValueError(msg)
        if self.order_type == OrderType.STOP and self.stop_price is None:
            msg = "stop orders require stop_price"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# NoActionProposal (D-11)
# ---------------------------------------------------------------------------


class NoActionProposal(BaseModel):
    """The Decision agent declined to trade this cycle (D-11).

    First-class in the schema layer so the Decision agent cannot emit
    free-form "I'm not sure" text — it must populate ``factors_considered``
    and a ``confidence`` score so the v2 retrospective dashboard can still
    audit the no-action path.
    """

    model_config = ConfigDict(frozen=False, extra="ignore")

    user_id: str = Field(..., min_length=1)
    strategy_name: str = Field(..., min_length=1)
    decision_id: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1, max_length=5000)
    factors_considered: list[str] = Field(..., min_length=1, max_length=20)
    confidence: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))


# ---------------------------------------------------------------------------
# Type alias for either-or proposal callers
# ---------------------------------------------------------------------------


Proposal = TradeProposal | NoActionProposal
"""Union of the two Decision-agent outputs. Callers (Plan 01-07 ProposalWriter,
Plan 01-08 approval handler, Plan 01-09 dashboard) accept ``Proposal`` and
discriminate via ``isinstance`` checks rather than a Literal field — the two
shapes are different enough that a discriminator field would force unnecessary
common keys."""


# Convenience for downstream type narrowing.
NoActionLiteral = Literal["no_action"]
TradeLiteral = Literal["trade"]


__all__: tuple[str, ...] = (
    "AlternativeConsidered",
    "NoActionLiteral",
    "NoActionProposal",
    "Proposal",
    "TradeLiteral",
    "TradeProposal",
)
