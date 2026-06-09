"""Tool context injection — Plan 01-07 Task 3.

The Claude Agent SDK ``@tool`` decorator (per docs/sdk-shape.md delta #2)
requires tool functions to have the signature ``async def fn(args: dict)
-> dict`` — one positional ``args`` dict, returning the MCP content shape.

The SDK provides no kwargs-injection / dependency-injection hook on tool
calls. To pass per-run state (the BudgetTracker, the AlpacaBroker
instance) into the tool function bodies, we use module-globals scoped to
this module — :func:`set_tool_context` writes them before each run, and
each tool reads them via :func:`get_tool_context`.

**Single-event-loop assumption.** The Phase 1 runtime (Plan 01-07
``trigger_strategy_run``) holds the event loop until the run completes.
There is no cross-strategy interleaving in P1, so a module-global is
safe. P4 will revisit if/when continuous-loop strategies share the loop.

References:
  * docs/sdk-shape.md  delta #2 (no kwargs injection)
  * .planning/.../01-CONTEXT.md  D-18 (single-process modular monolith)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gekko.agent.budget import BudgetTracker
    from gekko.brokers.base import Brokerage


@dataclass
class ToolContext:
    """Per-run state shared across all Researcher tool invocations.

    ``budget`` accumulates token + call counts per RESEARCH §Pattern 1.
    ``broker`` is the concrete :class:`gekko.brokers.base.Brokerage` (P1:
    :class:`gekko.brokers.alpaca.AlpacaBroker`) the tools call for quotes.

    Both are required — tools raise :exc:`RuntimeError` if the context has
    not been set when they fire.
    """

    budget: BudgetTracker
    broker: Brokerage | None


_CONTEXT: ToolContext | None = None


def set_tool_context(*, budget: BudgetTracker, broker: Brokerage | None = None) -> None:
    """Set the per-run tool context.

    Called by ``trigger_strategy_run`` BEFORE invoking the Researcher
    subagent. The ``broker`` may be ``None`` for tools that don't need it
    (e.g., ``finnhub_news``, ``edgar``, ``web_fetch``) — only
    :func:`gekko.agent.tools.alpaca_data.get_quote` requires a broker.

    :param budget: The :class:`BudgetTracker` for this cycle. Each tool
        invocation calls ``budget.record_call(tokens=<estimate>)``.
    :param broker: Optional :class:`Brokerage` (typically AlpacaBroker).
        ``get_quote`` reads it; other tools ignore it.
    """
    global _CONTEXT
    _CONTEXT = ToolContext(budget=budget, broker=broker)


def get_tool_context() -> ToolContext:
    """Return the active per-run tool context.

    :raises RuntimeError: When no context has been set (the caller forgot
        to call :func:`set_tool_context` before invoking the SDK).
    """
    if _CONTEXT is None:
        msg = (
            "tool context not set; call set_tool_context(budget=..., broker=...) "
            "before invoking the Researcher subagent"
        )
        raise RuntimeError(msg)
    return _CONTEXT


def clear_tool_context() -> None:
    """Reset the tool context. Test-only hygiene helper."""
    global _CONTEXT
    _CONTEXT = None


__all__: tuple[str, ...] = (
    "ToolContext",
    "clear_tool_context",
    "get_tool_context",
    "set_tool_context",
)
