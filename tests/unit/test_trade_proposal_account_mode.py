"""BLOCKER #5 TradeProposal.account_mode required Literal field — Plan 02-01 Task 3.

Closes the TOCTOU window between proposal-gen (T0) and approve-click (T1):
account_mode is required at construction time, locked in the proposal row,
and immune to post-stamp strategy.mode changes.

7 behaviors:
  1. instantiating without account_mode raises ValidationError
  2. account_mode="PAPER" validates
  3. account_mode="LIVE" validates
  4. account_mode="paper" (lowercase) raises (Literal case-sensitive)
  5. account_mode="MARGIN" raises (not in allowed values)
  6. "account_mode" IS in _runtime_only tuple — ProposalWriter stamps it
  7. "account_mode" is NOT in _PROPOSE_TRADE_SCHEMA properties (stripped)
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from gekko.schemas.research import EvidenceSnippet
from gekko.schemas.proposal import AlternativeConsidered


def _evidence(count: int) -> list[EvidenceSnippet]:
    return [
        EvidenceSnippet(
            source_type="finnhub_news",
            source_url="https://finnhub.io/news/x",
            fetched_at="2026-06-09T15:00:00+00:00",
            summary=f"evidence {i}",
        )
        for i in range(count)
    ]


def _alts(count: int) -> list[AlternativeConsidered]:
    return [
        AlternativeConsidered(
            description=f"Wait for next earnings ({i})",
            why_rejected=f"Earnings 3 weeks out ({i})",
        )
        for i in range(count)
    ]


def _kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "user_id": "alice",
        "strategy_name": "ai-infra",
        "decision_id": "d1",
        "ticker": "NVDA",
        "side": "buy",
        "qty": Decimal("5"),
        "target_notional_usd": Decimal("1000.00"),
        "order_type": "limit",
        "limit_price": Decimal("200.00"),
        "stop_price": None,
        "rationale": "Strong technicals.",
        "confidence": Decimal("0.7"),
        "evidence": _evidence(3),
        "alternatives_considered": _alts(1),
        "client_order_id": "a" * 32,
        "account_mode": "PAPER",
    }
    base.update(overrides)
    return base


def test_account_mode_missing_raises() -> None:
    """Behavior 1: missing field raises ValidationError."""
    from gekko.schemas.proposal import TradeProposal

    kwargs = _kwargs()
    kwargs.pop("account_mode")
    with pytest.raises(ValidationError) as exc_info:
        TradeProposal(**kwargs)  # type: ignore[arg-type]
    assert "account_mode" in str(exc_info.value)


def test_account_mode_paper_validates() -> None:
    """Behavior 2: account_mode='PAPER' validates."""
    from gekko.schemas.proposal import TradeProposal

    tp = TradeProposal(**_kwargs(account_mode="PAPER"))  # type: ignore[arg-type]
    assert tp.account_mode == "PAPER"


def test_account_mode_live_validates() -> None:
    """Behavior 3: account_mode='LIVE' validates."""
    from gekko.schemas.proposal import TradeProposal

    tp = TradeProposal(**_kwargs(account_mode="LIVE"))  # type: ignore[arg-type]
    assert tp.account_mode == "LIVE"


def test_account_mode_lowercase_rejected() -> None:
    """Behavior 4: account_mode='paper' (lowercase) raises — Literal is case-sensitive."""
    from gekko.schemas.proposal import TradeProposal

    with pytest.raises(ValidationError) as exc_info:
        TradeProposal(**_kwargs(account_mode="paper"))  # type: ignore[arg-type]
    assert "account_mode" in str(exc_info.value)


def test_account_mode_margin_rejected() -> None:
    """Behavior 5: account_mode='MARGIN' raises — not in allowed values."""
    from gekko.schemas.proposal import TradeProposal

    with pytest.raises(ValidationError) as exc_info:
        TradeProposal(**_kwargs(account_mode="MARGIN"))  # type: ignore[arg-type]
    assert "account_mode" in str(exc_info.value)


def test_account_mode_not_in_propose_trade_schema_properties() -> None:
    """Behavior 7: account_mode is stripped — LLM does NOT see it.

    ProposalWriter (plan 02-06 Task 2) stamps it from strategy state at
    proposal-build time. The LLM cannot author account_mode because that
    would open the TOCTOU window BLOCKER #5 closes.
    """
    from gekko.agent.tools.propose_trade import _PROPOSE_TRADE_SCHEMA

    assert "account_mode" not in _PROPOSE_TRADE_SCHEMA["properties"]
    assert "account_mode" not in _PROPOSE_TRADE_SCHEMA["required"]


def test_account_mode_in_runtime_only_tuple() -> None:
    """Behavior 6: account_mode is in _runtime_only — ProposalWriter stamps it.

    We re-derive the schema with a fresh helper call. If account_mode shows
    up in properties, _runtime_only doesn't include it (regression catch).
    """
    from gekko.agent.tools.propose_trade import _build_propose_trade_schema

    schema = _build_propose_trade_schema()
    assert "account_mode" not in schema["properties"], (
        "account_mode leaked into LLM-visible schema — BLOCKER #5 TOCTOU "
        "window reopened. Add 'account_mode' to _runtime_only in "
        "propose_trade._build_propose_trade_schema."
    )
