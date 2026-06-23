"""Pricing constants + ``tokens_to_usd()`` fallback formula — Plan 04-02 Task 2.

This module is the canonical location for all LLM model pricing data (D-10
discretion decision). Every component that needs to estimate or reference the
cost of a model call imports from here — no $/MTok literals anywhere else.

Usage hierarchy:
1. **Prefer** ``ResultMessage.total_cost_usd`` from the Claude Agent SDK — it is
   the CLI's own authoritative figure and accounts for prompt caching, cache
   write charges, and any real-time pricing adjustments.
2. **Fall back** to ``tokens_to_usd(input_tokens, output_tokens, model=...)``
   when ``total_cost_usd`` is ``None`` (CI mock mode, future model not yet
   in SDK pricing table, etc.).

``DEFAULT_DAILY_CEILING_USD`` is defined here as the single authoritative
constant for code that needs to reference the D-02 default programmatically.
The migration 0005 ``server_default='5.00'`` and the Settings form placeholder
are derived copies — this module is the named source.

Decimal discipline:
All constants and the formula use ``decimal.Decimal`` (not ``float``) per the
project-wide money-math convention (Plan 01-04 D-10 rationale). The formula
formula uses integer-safe arithmetic: ``Decimal(int) / MTOK * rate`` rather
than constructing Decimal from a float intermediate.
"""

from __future__ import annotations

from decimal import Decimal

# ---------------------------------------------------------------------------
# Per-model pricing constants
# Source: platform.claude.com/docs/en/about-claude/pricing
# Verified: 2026-06-23
#
# Note: "sonnet" and "haiku" are SDK model aliases that resolve to the
# current latest Sonnet / Haiku release. When Anthropic releases a new
# generation, the alias re-resolves and these constants may need updating.
# The SDK's ResultMessage.total_cost_usd is always authoritative for
# actual charges; these constants are the formula-fallback only.
# ---------------------------------------------------------------------------

SONNET_INPUT_PER_MTOK: Decimal = Decimal("3.00")    # $/MTok input tokens
SONNET_OUTPUT_PER_MTOK: Decimal = Decimal("15.00")  # $/MTok output tokens
HAIKU_INPUT_PER_MTOK: Decimal = Decimal("1.00")     # $/MTok input tokens
HAIKU_OUTPUT_PER_MTOK: Decimal = Decimal("5.00")    # $/MTok output tokens

# D-02 configurable default; override via Settings UI (stored in users.daily_cost_ceiling_usd).
# The migration 0005 server_default='5.00' and the Settings form placeholder='5.00'
# are derived copies — this constant is the single named source for code.
DEFAULT_DAILY_CEILING_USD: Decimal = Decimal("5.00")

# Internal divisor — 1 million tokens per MTok (integer-exact in Decimal).
_MTOK: Decimal = Decimal("1000000")

# Dispatch table: model alias → (input_rate, output_rate)
_MODEL_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "sonnet": (SONNET_INPUT_PER_MTOK, SONNET_OUTPUT_PER_MTOK),
    "haiku": (HAIKU_INPUT_PER_MTOK, HAIKU_OUTPUT_PER_MTOK),
}


def tokens_to_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    model: str = "sonnet",
) -> Decimal:
    """Compute USD cost from token counts using the per-model $/MTok rates.

    This is the **fallback formula** used when ``ResultMessage.total_cost_usd``
    is ``None``. For production cost accounting, prefer the SDK-provided figure.

    :param input_tokens: Number of input (prompt) tokens consumed.
    :param output_tokens: Number of output (completion) tokens generated.
    :param model: Model alias string — ``"sonnet"`` or ``"haiku"``.
        Matches the alias accepted by ``ClaudeAgentOptions(model=...)``.
    :returns: Computed cost as a ``Decimal`` (USD).
    :raises ValueError: If ``model`` is not a recognised alias. Fails loudly
        rather than silently returning $0 so callers notice unsupported models.

    Formula::

        cost = (input_tokens / 1_000_000) * input_rate
             + (output_tokens / 1_000_000) * output_rate
    """
    rates = _MODEL_RATES.get(model)
    if rates is None:
        supported = ", ".join(f'"{k}"' for k in _MODEL_RATES)
        msg = (
            f"Unknown model alias {model!r} — supported: {supported}. "
            "Add the new model's $/MTok rates to gekko.agent.pricing."
        )
        raise ValueError(msg)

    input_rate, output_rate = rates
    return (
        (Decimal(input_tokens) / _MTOK) * input_rate
        + (Decimal(output_tokens) / _MTOK) * output_rate
    )


__all__: tuple[str, ...] = (
    "SONNET_INPUT_PER_MTOK",
    "SONNET_OUTPUT_PER_MTOK",
    "HAIKU_INPUT_PER_MTOK",
    "HAIKU_OUTPUT_PER_MTOK",
    "DEFAULT_DAILY_CEILING_USD",
    "tokens_to_usd",
)
