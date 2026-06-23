"""Pricing constants + tokens_to_usd() stubs — Phase 4 Wave 0.

Covers:
  - SONNET_INPUT_PER_MTOK == Decimal("3.00")
  - SONNET_OUTPUT_PER_MTOK == Decimal("15.00")
  - HAIKU_INPUT_PER_MTOK == Decimal("1.00")
  - HAIKU_OUTPUT_PER_MTOK == Decimal("5.00")
  - tokens_to_usd(1_000_000, 0, model="sonnet") == Decimal("3.00")
  - tokens_to_usd(1_000_000, 0, model="haiku") == Decimal("1.00")
  - DEFAULT_DAILY_CEILING_USD == Decimal("5.00")

All tests import from ``gekko.agent.pricing`` which does NOT YET EXIST —
they will fail with ImportError on pytest collect, giving an unambiguous
RED signal until Wave 2 ships the module.
"""

from __future__ import annotations

from decimal import Decimal

# ---------------------------------------------------------------------------
# Import not-yet-existing symbols — intentional RED on collect
# ---------------------------------------------------------------------------
from gekko.agent.pricing import (  # noqa: F401
    DEFAULT_DAILY_CEILING_USD,
    HAIKU_INPUT_PER_MTOK,
    HAIKU_OUTPUT_PER_MTOK,
    SONNET_INPUT_PER_MTOK,
    SONNET_OUTPUT_PER_MTOK,
    tokens_to_usd,
)


# ---------------------------------------------------------------------------
# Tests — pricing constants (Anthropic pricing page, verified 2026-06-23)
# Source: platform.claude.com/docs/en/about-claude/pricing
# ---------------------------------------------------------------------------


def test_sonnet_input_price() -> None:
    """SONNET_INPUT_PER_MTOK matches Anthropic pricing: $3.00 per million input tokens."""
    assert SONNET_INPUT_PER_MTOK == Decimal("3.00")


def test_sonnet_output_price() -> None:
    """SONNET_OUTPUT_PER_MTOK matches Anthropic pricing: $15.00 per million output tokens."""
    assert SONNET_OUTPUT_PER_MTOK == Decimal("15.00")


def test_haiku_input_price() -> None:
    """HAIKU_INPUT_PER_MTOK matches Anthropic pricing: $1.00 per million input tokens."""
    assert HAIKU_INPUT_PER_MTOK == Decimal("1.00")


def test_haiku_output_price() -> None:
    """HAIKU_OUTPUT_PER_MTOK matches Anthropic pricing: $5.00 per million output tokens."""
    assert HAIKU_OUTPUT_PER_MTOK == Decimal("5.00")


# ---------------------------------------------------------------------------
# Tests — tokens_to_usd() formula
# ---------------------------------------------------------------------------


def test_tokens_to_usd_sonnet() -> None:
    """1 MTok input + 0 output with model='sonnet' costs exactly $3.00."""
    result = tokens_to_usd(1_000_000, 0, model="sonnet")
    assert result == Decimal("3.00")


def test_tokens_to_usd_haiku() -> None:
    """1 MTok input + 0 output with model='haiku' costs exactly $1.00."""
    result = tokens_to_usd(1_000_000, 0, model="haiku")
    assert result == Decimal("1.00")


# ---------------------------------------------------------------------------
# Tests — default ceiling constant
# ---------------------------------------------------------------------------


def test_default_ceiling_constant() -> None:
    """DEFAULT_DAILY_CEILING_USD ships with value Decimal('5.00') (D-02)."""
    assert DEFAULT_DAILY_CEILING_USD == Decimal("5.00")
