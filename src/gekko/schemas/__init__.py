"""Pydantic schema contracts — Plan 01-06.

Single source of truth for the inter-module shapes every Wave 2 plan
(01-07 agent runtime, 01-08 Slack/executor, 01-09 CLI/dashboard) imports.
"""

from gekko.schemas.diff import compute_field_changes, generate_strategy_diff
from gekko.schemas.research import (
    EvidenceSnippet,
    EvidenceSourceType,
    ResearchBrief,
    TickerSnapshot,
)
from gekko.schemas.strategy import Guidance, HardCaps, Strategy, next_version

__all__: tuple[str, ...] = (
    "EvidenceSnippet",
    "EvidenceSourceType",
    "Guidance",
    "HardCaps",
    "ResearchBrief",
    "Strategy",
    "TickerSnapshot",
    "compute_field_changes",
    "generate_strategy_diff",
    "next_version",
)
