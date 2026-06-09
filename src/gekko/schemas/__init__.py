"""Pydantic schema contracts — Plan 01-06.

Single source of truth for the inter-module shapes every Wave 2 plan
(01-07 agent runtime, 01-08 Slack/executor, 01-09 CLI/dashboard) imports.
"""

from gekko.schemas.diff import compute_field_changes, generate_strategy_diff
from gekko.schemas.event import (
    ApprovalEventPayload,
    CapRejectionEventPayload,
    DecisionEventPayload,
    ErrorEventPayload,
    EventPayload,
    FillEventPayload,
    KillSwitchEventPayload,
    OrderSubmittedEventPayload,
    ProposalEventPayload,
    RejectionEventPayload,
)
from gekko.schemas.proposal import (
    AlternativeConsidered,
    NoActionProposal,
    Proposal,
    TradeProposal,
)
from gekko.schemas.research import (
    EvidenceSnippet,
    EvidenceSourceType,
    ResearchBrief,
    TickerSnapshot,
)
from gekko.schemas.strategy import Guidance, HardCaps, Strategy, next_version

__all__: tuple[str, ...] = (
    # strategy
    "Guidance",
    "HardCaps",
    "Strategy",
    "next_version",
    # diff
    "compute_field_changes",
    "generate_strategy_diff",
    # research
    "EvidenceSnippet",
    "EvidenceSourceType",
    "ResearchBrief",
    "TickerSnapshot",
    # proposal
    "AlternativeConsidered",
    "NoActionProposal",
    "Proposal",
    "TradeProposal",
    # event
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
