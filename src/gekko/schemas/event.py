"""Typed EventPayload helpers — Plan 01-06 Task 3.

The ``events.payload_json`` column (Plan 01-03 schema; Plan 01-04 writer)
stores a canonical-subset JSON string. This module provides Pydantic-typed
shapes for each ``event_type`` discriminator value (D-14) so write-site
callers (Plans 01-07 / 01-08) can construct payloads with type safety, and
read-site callers (Plan 01-09 ``gekko audit verify``, dashboards) can
deserialize into typed accessors.

Approach: a discriminated union over ``event_kind`` using Pydantic v2's
``Discriminator``. The audit log writer (``append_event``) accepts a plain
dict so Plan 01-04's API contract is unchanged — this module is the
**caller-side** typed validator: callers build the typed payload, call
``.model_dump()``, and pass the dict to ``append_event``.

D-14 event_type vocabulary covered:

* decision
* proposal
* approval
* rejection
* order_submitted
* fill
* kill_switch (P-future; minimal shape here)
* cap_rejection (P2 OrderGuard; minimal shape here)
* error

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-14, D-15
  * src/gekko/db/models.py  (Event.event_type CheckConstraint vocabulary)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

# ---------------------------------------------------------------------------
# Per-event_kind payload models
# ---------------------------------------------------------------------------


class _BasePayload(BaseModel):
    """Shared config base — every variant uses extra='ignore' for forward-compat."""

    model_config = ConfigDict(frozen=False, extra="ignore")


class DecisionEventPayload(_BasePayload):
    """``event_type='decision'`` payload — per D-15 references the research brief."""

    event_kind: Literal["decision"] = "decision"
    run_id: str
    strategy_id: str
    prompt_model: str
    research_brief_run_id: str
    decision_outcome: Literal["trade", "no_action"]


class ProposalEventPayload(_BasePayload):
    """``event_type='proposal'`` payload — D-15 says this IS the proposal model_dump."""

    event_kind: Literal["proposal"] = "proposal"
    # The full TradeProposal.model_dump() or NoActionProposal.model_dump() — kept as
    # dict so either shape fits; the audit log walk_chain doesn't try to re-parse.
    proposal: dict[str, Any]


class ApprovalEventPayload(_BasePayload):
    """``event_type='approval'`` payload."""

    event_kind: Literal["approval"] = "approval"
    proposal_id: str
    actor: str  # slack user_id or "system:..."
    slack_action_id: str | None = None


class RejectionEventPayload(_BasePayload):
    """``event_type='rejection'`` payload."""

    event_kind: Literal["rejection"] = "rejection"
    proposal_id: str
    actor: str
    reason: str | None = None


class OrderSubmittedEventPayload(_BasePayload):
    """``event_type='order_submitted'`` payload — broker call returned an order id."""

    event_kind: Literal["order_submitted"] = "order_submitted"
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    qty: str  # Decimal serialized as string (caller normalizes before persist)
    order_type: str


class FillEventPayload(_BasePayload):
    """``event_type='fill'`` payload — websocket fill arrived from the broker."""

    event_kind: Literal["fill"] = "fill"
    client_order_id: str
    broker_order_id: str
    filled_qty: str
    filled_avg_price: str
    ticker: str


class KillSwitchEventPayload(_BasePayload):
    """``event_type='kill_switch'`` payload — P3/P4 will deepen; minimal P1 shape."""

    event_kind: Literal["kill_switch"] = "kill_switch"
    actor: str
    reason: str


class CapRejectionEventPayload(_BasePayload):
    """``event_type='cap_rejection'`` payload — P2 OrderGuard emits these."""

    event_kind: Literal["cap_rejection"] = "cap_rejection"
    proposal_id: str
    cap_name: str  # e.g., "max_position_pct", "max_daily_loss_usd"
    cap_value: str
    attempted_value: str


class ErrorEventPayload(_BasePayload):
    """``event_type='error'`` payload — any caught + logged error path."""

    event_kind: Literal["error"] = "error"
    context: str
    error_class: str
    error_message: str


# ---------------------------------------------------------------------------
# Discriminated union (Pydantic v2)
# ---------------------------------------------------------------------------


def _extract_event_kind(v: Any) -> str | None:
    """Discriminator function: returns the value of ``event_kind`` from a dict or model.

    Returns None on missing key so Pydantic surfaces a structured discrimination
    error rather than a KeyError.
    """
    if isinstance(v, dict):
        return v.get("event_kind")
    return getattr(v, "event_kind", None)


EventPayload = Annotated[
    Annotated[DecisionEventPayload, Tag("decision")]
    | Annotated[ProposalEventPayload, Tag("proposal")]
    | Annotated[ApprovalEventPayload, Tag("approval")]
    | Annotated[RejectionEventPayload, Tag("rejection")]
    | Annotated[OrderSubmittedEventPayload, Tag("order_submitted")]
    | Annotated[FillEventPayload, Tag("fill")]
    | Annotated[KillSwitchEventPayload, Tag("kill_switch")]
    | Annotated[CapRejectionEventPayload, Tag("cap_rejection")]
    | Annotated[ErrorEventPayload, Tag("error")],
    Discriminator(_extract_event_kind),
    Field(description="D-14 discriminated union over event_kind"),
]
"""Discriminated union over the D-14 ``event_type`` vocabulary.

Use via ``pydantic.TypeAdapter``:

    from pydantic import TypeAdapter
    from gekko.schemas.event import EventPayload

    adapter = TypeAdapter(EventPayload)
    payload = adapter.validate_python({"event_kind": "fill", ...})

Plans 01-07 and 01-08 use this at the write site to validate payload shape
BEFORE passing to ``gekko.audit.log.append_event`` (which itself accepts a
plain dict — Plan 01-04's contract is unchanged).
"""


__all__: tuple[str, ...] = (
    "ApprovalEventPayload",
    "CapRejectionEventPayload",
    "DecisionEventPayload",
    "ErrorEventPayload",
    "EventPayload",
    "FillEventPayload",
    "KillSwitchEventPayload",
    "OrderSubmittedEventPayload",
    "ProposalEventPayload",
    "RejectionEventPayload",
)
