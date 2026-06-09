"""``BudgetTracker`` — per-cycle research budget enforcement — Plan 01-07 Task 2.

Per CONTEXT.md D-13: per-cycle research budget is soft + 2x grace. The
*soft* threshold (12 tool calls / 8000 tokens / 60s wall time) emits a
structlog warning so operators see the cycle is running hot. The *hard*
threshold (2x any soft cap) raises :exc:`gekko.core.errors.BudgetExceeded`,
which the Researcher subagent must surface to halt further tool calls.

The hard 2x backstop is the only application-layer guard against a runaway
research loop in Phase 1. The daily-cost ceiling is Phase 4's concern; until
that lands, this per-cycle 2x halt IS the catastrophic-loss prevention layer
for the LLM spend dimension (the *trade-loss* dimension is the OrderGuard's
job in Phase 2).

Token accounting in P1 is *approximate*: each Researcher tool calls
``record_call(tokens=<estimate>)`` with a flat per-tool estimate from
RESEARCH §"Token-cost estimates" (100/200/300/500 for get_quote / get_news /
get_edgar_filing / web_fetch respectively). Phase 4 will refine this with the
actual ``ResultMessage.usage`` figures the SDK returns per turn (docs/sdk-shape.md
delta #6).

References:
  * .planning/phases/01-foundation.../01-RESEARCH.md  §Pattern 1
  * .planning/phases/01-foundation.../01-CONTEXT.md   D-13
  * docs/sdk-shape.md                                  delta #6 (token usage)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from gekko.core.errors import BudgetExceeded
from gekko.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class BudgetTracker:
    """Per-cycle research budget with soft warning + 2x hard halt.

    Soft thresholds (D-13):

    * ``soft_max_calls = 12``     — tool calls
    * ``soft_max_tokens = 8000``  — total tokens recorded across the cycle
    * ``soft_max_seconds = 60.0`` — wall-clock elapsed since construction

    Hard threshold: 2x any soft cap raises :exc:`BudgetExceeded`.

    Custom caps (e.g., a tighter budget for paper-mode smoke tests) can be
    passed at construction. The 2x rule applies to the customized caps, so
    ``BudgetTracker(soft_max_calls=5)`` halts at 11 calls (>10).

    The tracker captures ``started_at`` via :func:`time.monotonic` in the
    field default factory. Tests that need to simulate elapsed wall time
    monkeypatch :func:`time.monotonic` on this module — note the module-
    qualified import is necessary so the patch reaches the live reference.
    """

    soft_max_calls: int = 12
    soft_max_tokens: int = 8000
    soft_max_seconds: float = 60.0
    started_at: float = field(default_factory=lambda: time.monotonic())
    calls: int = 0
    tokens_used: int = 0

    def record_call(self, tokens: int) -> None:
        """Record a single tool call and check soft/hard thresholds.

        :param tokens: Approximate token cost of the call. Researcher tools
            pass the flat per-tool estimate from RESEARCH §"Token-cost
            estimates"; future P4 hardening can refine via the SDK's
            per-turn ``ResultMessage.usage`` figures.

        :raises BudgetExceeded: When *any* counter exceeds 2x its soft cap.
            The exception message embeds the offending counter values so
            operators can pinpoint which dimension blew the budget.

        Soft-threshold warnings are emitted via structlog with the event
        name ``research.budget.soft_exceeded`` and structured fields
        ``calls``, ``tokens``, and ``elapsed``. The warning is logged-only
        (no raise) — the hard halt below is the load-bearing guard.
        """
        self.calls += 1
        self.tokens_used += tokens
        elapsed = time.monotonic() - self.started_at

        if (
            self.calls > self.soft_max_calls
            or self.tokens_used > self.soft_max_tokens
            or elapsed > self.soft_max_seconds
        ):
            log.warning(
                "research.budget.soft_exceeded",
                calls=self.calls,
                tokens=self.tokens_used,
                elapsed=elapsed,
            )

        if (
            self.calls > 2 * self.soft_max_calls
            or self.tokens_used > 2 * self.soft_max_tokens
            or elapsed > 2 * self.soft_max_seconds
        ):
            msg = (
                f"per-cycle budget 2x exceeded: "
                f"calls={self.calls}, tokens={self.tokens_used}, "
                f"seconds={elapsed:.1f}"
            )
            raise BudgetExceeded(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the tracker's state.

        The shape ``{"calls", "tokens", "seconds"}`` matches the
        ``research_budget_used`` field on :class:`gekko.schemas.research.ResearchBrief`
        — the Researcher subagent attaches this to the brief so the audit
        log captures per-cycle cost.
        """
        return {
            "calls": self.calls,
            "tokens": self.tokens_used,
            "seconds": time.monotonic() - self.started_at,
        }


__all__: tuple[str, ...] = ("BudgetTracker",)
