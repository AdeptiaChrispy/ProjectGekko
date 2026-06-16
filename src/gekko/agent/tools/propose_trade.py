"""``propose_trade`` Decision tool — Plan 01-07 Task 4.

Per D-11 the Decision subagent has a 2-tool list: this tool, and
``propose_no_action`` (sibling module). Per D-12 it enforces the
structured-rationale shape (3-5 evidence + 1+ alternatives + confidence)
via the input schema — the LLM cannot emit a TradeProposal without those
fields.

Per docs/sdk-shape.md deltas #1 and #2: ``@tool`` is positional,
``async def fn(args: dict) -> dict`` returns MCP content shape.

The tool body is a **sentinel return**: the actual persistence happens
in :func:`gekko.agent.proposal_writer.write_proposal` (Task 5). This
tool exists to (a) get the LLM into Pydantic-validated tool-call mode
per the SDK and (b) carry the structured payload back to the orchestrator
where the runtime extracts the ``ToolUseBlock.input`` dict directly per
docs/sdk-shape.md delta #6.

Note on the input schema: we derive it from :class:`TradeProposal` but
*strip* four fields the LLM does NOT supply:

* ``user_id`` — set by runtime from the trigger context
* ``strategy_name`` — set by runtime
* ``decision_id`` — set by runtime (UUID per run)
* ``client_order_id`` — set by runtime via :func:`compute_client_order_id`

D-20 lives at the ProposalWriter, not the LLM — the deterministic
idempotency key is computed, not chosen.

References:
  * .planning/.../01-CONTEXT.md  D-11, D-12, D-20
  * .planning/.../01-RESEARCH.md  §"Anti-Patterns" (Decision tool list = exactly 2)
  * docs/sdk-shape.md             deltas #1, #2, #6
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from gekko.schemas.proposal import TradeProposal

# ---------------------------------------------------------------------------
# Input-schema construction: derive from TradeProposal, then strip runtime-
# computed fields the LLM does NOT supply.
# ---------------------------------------------------------------------------


def _build_propose_trade_schema() -> dict[str, Any]:
    """Return a JSON-Schema dict for the LLM-visible subset of TradeProposal.

    We:

    1. Start from ``TradeProposal.model_json_schema()`` which yields a full
       JSON Schema dict with ``properties``, ``required``, ``$defs``.
    2. Remove runtime-computed fields from both ``properties`` and
       ``required``: ``user_id``, ``strategy_name``, ``decision_id``,
       ``client_order_id``, ``account_mode`` (BLOCKER #5 — stamped by
       ProposalWriter from strategy.mode at proposal-build time to close
       the TOCTOU window; the LLM cannot author it), and ``wash_sale_flag``
       (populated by OrderGuard runtime in plan 02-03).

    The remaining schema is what the LLM sees in its tool-use prompt.

    NB: ``target_notional_usd`` (D-27) is LLM-authored and NOT in
    ``_runtime_only`` — the Decision agent declares its dollar intent and
    OrderGuard checks ``qty * ref_price`` against it.
    """
    schema: dict[str, Any] = dict(TradeProposal.model_json_schema())
    _runtime_only = (
        "user_id",
        "strategy_name",
        "decision_id",
        "client_order_id",
        # BLOCKER #5 — account_mode is runtime-stamped from strategy state.
        "account_mode",
        # Plan 02-03 — OrderGuard populates this flag, not the LLM.
        "wash_sale_flag",
    )
    props = dict(schema.get("properties", {}))
    for f in _runtime_only:
        props.pop(f, None)
    schema["properties"] = props
    required = [f for f in schema.get("required", []) if f not in _runtime_only]
    schema["required"] = required
    return schema


_PROPOSE_TRADE_SCHEMA: dict[str, Any] = _build_propose_trade_schema()


@tool(
    "propose_trade",
    (
        "Propose a trade for human approval. Requires 3-5 evidence snippets, "
        "at least one alternative considered, and a confidence score in [0,1]. "
        "Ticker MUST be in the strategy's watchlist. The decision_id, "
        "user_id, strategy_name, client_order_id, and account_mode are filled "
        "by the runtime. Provide `target_notional_usd` as your dollar intent — "
        "OrderGuard rejects if `qty * ref_price` drifts > 2% from this value."
    ),
    _PROPOSE_TRADE_SCHEMA,
)
async def propose_trade(args: dict[str, Any]) -> dict[str, Any]:
    """Sentinel-return Decision tool.

    The runtime extracts ``ToolUseBlock.input`` from the Decision agent's
    AssistantMessage directly and hands it to :func:`write_proposal`. The
    SDK still requires a return value from the tool function — we emit a
    structured echo so the Decision agent receives confirmation in its
    response stream and stops.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"_tool_outcome": "propose_trade", "received": True}
                ),
            }
        ],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("propose_trade",)
