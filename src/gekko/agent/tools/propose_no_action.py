"""``propose_no_action`` Decision tool — Plan 01-07 Task 4.

Per D-11 ``no_action`` is **first-class** — the Decision subagent cannot
fall back to free-form text when the brief doesn't support a trade. The
schema enforces ``min_length=1`` ``factors_considered`` + ``confidence``
in [0,1].

Same sentinel-return pattern as :mod:`propose_trade`. The runtime extracts
``ToolUseBlock.input`` from the Decision agent's AssistantMessage and
hands it to :func:`write_proposal`.

Runtime-computed fields the LLM does NOT supply:
``user_id``, ``strategy_name``, ``decision_id``.

References:
  * .planning/.../01-CONTEXT.md  D-09 (verbose no_action), D-11
  * docs/sdk-shape.md             deltas #1, #2, #6
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from gekko.schemas.proposal import NoActionProposal


def _build_propose_no_action_schema() -> dict[str, Any]:
    """Derive the LLM-visible JSON Schema from NoActionProposal.

    Strips runtime-computed fields ``user_id``, ``strategy_name``,
    ``decision_id``.
    """
    schema: dict[str, Any] = dict(NoActionProposal.model_json_schema())
    _runtime_only = ("user_id", "strategy_name", "decision_id")
    props = dict(schema.get("properties", {}))
    for f in _runtime_only:
        props.pop(f, None)
    schema["properties"] = props
    required = [f for f in schema.get("required", []) if f not in _runtime_only]
    schema["required"] = required
    return schema


_PROPOSE_NO_ACTION_SCHEMA: dict[str, Any] = _build_propose_no_action_schema()


@tool(
    "propose_no_action",
    (
        "Decline to trade this cycle. Required when evidence is thin, the "
        "thesis isn't met today, or risk doesn't justify entry. First-class "
        "per D-11. Must include factors_considered (at least 1, max 20) and "
        "a confidence score in [0,1]."
    ),
    _PROPOSE_NO_ACTION_SCHEMA,
)
async def propose_no_action(args: dict[str, Any]) -> dict[str, Any]:
    """Sentinel-return Decision tool — see propose_trade module docstring."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"_tool_outcome": "propose_no_action", "received": True}
                ),
            }
        ],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("propose_no_action",)
