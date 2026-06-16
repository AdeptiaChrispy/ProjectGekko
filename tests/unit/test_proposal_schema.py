"""Tests for ``gekko.schemas.proposal`` + ``gekko.schemas.event`` — Plan 01-06 Task 3.

Enforces D-11 (no_action first-class) + D-12 (3-5 evidence, 1+ alternatives,
confidence 0..1) + REPT-04 (structured rationale captured at the schema layer).
D-15 says the audit log's ``payload_json`` IS the model_dump of these
proposals — so the schema constraints are the rationale-capture invariant.

References:
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Code Examples — TradeProposal / NoActionProposal"
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-11, D-12, D-14, D-15
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _evidence(count: int) -> list[object]:
    from gekko.schemas.research import EvidenceSnippet

    return [
        EvidenceSnippet(
            source_type="finnhub_news",
            source_url="https://finnhub.io/news/x",
            fetched_at="2026-06-09T15:00:00+00:00",
            summary=f"evidence {i}",
        )
        for i in range(count)
    ]


def _alts(count: int) -> list[object]:
    from gekko.schemas.proposal import AlternativeConsidered

    return [
        AlternativeConsidered(
            description=f"Wait for next earnings ({i})",
            why_rejected=f"Earnings 3 weeks out; technicals stronger ({i})",
        )
        for i in range(count)
    ]


def _trade_proposal_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "user_id": "alice",
        "strategy_name": "ai-infra",
        "decision_id": "d1",
        "ticker": "NVDA",
        "side": "buy",
        "qty": Decimal("5"),
        # Plan 02-01 Task 3 (D-27): LLM-authored dollar intent for OrderGuard.
        "target_notional_usd": Decimal("6173.00"),  # ~ 5 * 1234.56
        "order_type": "limit",
        "limit_price": Decimal("1234.56"),
        "stop_price": None,
        "rationale": "Strong technicals; sector beat.",
        "confidence": Decimal("0.7"),
        "evidence": _evidence(3),
        "alternatives_considered": _alts(1),
        "client_order_id": "a" * 32,
        # Plan 02-01 Task 3 (BLOCKER #5): account_mode is required + runtime-stamped.
        "account_mode": "PAPER",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AlternativeConsidered
# ---------------------------------------------------------------------------


class TestAlternativeConsidered:
    def test_valid_construction(self) -> None:
        from gekko.schemas.proposal import AlternativeConsidered

        a = AlternativeConsidered(
            description="Wait for next earnings",
            why_rejected="Earnings 3 weeks out; current technicals stronger",
        )
        assert "earnings" in a.description.lower()


# ---------------------------------------------------------------------------
# TradeProposal
# ---------------------------------------------------------------------------


class TestTradeProposal:
    def test_valid_construction(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        p = TradeProposal(**_trade_proposal_kwargs())  # type: ignore[arg-type]
        assert p.ticker == "NVDA"
        assert p.qty == Decimal("5")
        assert len(p.evidence) == 3
        assert len(p.alternatives_considered) == 1

    def test_evidence_too_few_rejected(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(evidence=_evidence(2)))  # type: ignore[arg-type]

    def test_evidence_too_many_rejected(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(evidence=_evidence(6)))  # type: ignore[arg-type]

    def test_alternatives_empty_rejected(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(alternatives_considered=[]))  # type: ignore[arg-type]

    def test_side_must_be_buy_or_sell(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(side="hold"))  # type: ignore[arg-type]

    def test_qty_must_be_positive(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(qty=Decimal("0")))  # type: ignore[arg-type]

    def test_confidence_out_of_range_rejected(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(confidence=Decimal("1.5")))  # type: ignore[arg-type]

    def test_limit_order_requires_limit_price(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(
                **_trade_proposal_kwargs(order_type="limit", limit_price=None)  # type: ignore[arg-type]
            )

    def test_stop_order_requires_stop_price(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        with pytest.raises(ValidationError):
            TradeProposal(
                **_trade_proposal_kwargs(
                    order_type="stop", stop_price=None, limit_price=None
                )  # type: ignore[arg-type]
            )

    def test_market_order_accepts_limit_price_field(self) -> None:
        """market + limit_price set — schema accepts; executor reads order_type."""
        from gekko.schemas.proposal import TradeProposal

        p = TradeProposal(
            **_trade_proposal_kwargs(
                order_type="market", limit_price=Decimal("100")
            )  # type: ignore[arg-type]
        )
        # Field NOT stripped; executor (Plan 01-08) decides via order_type.
        assert p.limit_price == Decimal("100")
        assert p.order_type == "market"

    def test_client_order_id_must_be_32_chars(self) -> None:
        from gekko.schemas.proposal import TradeProposal

        # too short
        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(client_order_id="a" * 16))  # type: ignore[arg-type]
        # too long
        with pytest.raises(ValidationError):
            TradeProposal(**_trade_proposal_kwargs(client_order_id="a" * 64))  # type: ignore[arg-type]

    def test_model_dump_json_serializes_decimal_as_string(self) -> None:
        """Pydantic v2 default: Decimal → string in JSON output."""
        import json

        from gekko.schemas.proposal import TradeProposal

        p = TradeProposal(**_trade_proposal_kwargs())  # type: ignore[arg-type]
        payload = p.model_dump_json()
        parsed = json.loads(payload)
        # Decimal("5") -> "5" or 5.0 — but Pydantic v2 default is "5" (string).
        assert parsed["qty"] in ("5", "5.0")
        assert isinstance(parsed["qty"], (str, float, int))
        # confidence likewise
        assert str(parsed["confidence"]) in ("0.7", "7E-1")

    def test_forward_compat_extra_ignored(self) -> None:
        """extra='ignore' allows future fields without breaking deserialization."""
        from gekko.schemas.proposal import TradeProposal

        kwargs = _trade_proposal_kwargs(future_p4_field="ok")
        p = TradeProposal(**kwargs)  # type: ignore[arg-type]
        assert p.ticker == "NVDA"


# ---------------------------------------------------------------------------
# NoActionProposal
# ---------------------------------------------------------------------------


class TestNoActionProposal:
    def test_valid_construction(self) -> None:
        from gekko.schemas.proposal import NoActionProposal

        n = NoActionProposal(
            user_id="alice",
            strategy_name="ai-infra",
            decision_id="d1",
            rationale="No setup; thesis says wait.",
            factors_considered=["NVDA elevated", "thesis says wait"],
            confidence=Decimal("0.6"),
        )
        assert n.decision_id == "d1"
        assert len(n.factors_considered) == 2

    def test_factors_considered_empty_rejected(self) -> None:
        from gekko.schemas.proposal import NoActionProposal

        with pytest.raises(ValidationError):
            NoActionProposal(
                user_id="alice",
                strategy_name="ai-infra",
                decision_id="d1",
                rationale="No setup.",
                factors_considered=[],
                confidence=Decimal("0.6"),
            )

    def test_confidence_out_of_range(self) -> None:
        from gekko.schemas.proposal import NoActionProposal

        with pytest.raises(ValidationError):
            NoActionProposal(
                user_id="alice",
                strategy_name="ai-infra",
                decision_id="d1",
                rationale="No setup.",
                factors_considered=["x"],
                confidence=Decimal("-0.1"),
            )


# ---------------------------------------------------------------------------
# EventPayload discriminated union
# ---------------------------------------------------------------------------


class TestEventPayload:
    def test_decision_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "decision",
                "run_id": "r1",
                "strategy_id": "strat-abc",
                "prompt_model": "claude-sonnet-4-6",
                "research_brief_run_id": "rb1",
                "decision_outcome": "trade",
            }
        )
        assert payload.event_kind == "decision"  # type: ignore[union-attr]

    def test_proposal_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "proposal",
                "proposal": {"ticker": "NVDA", "side": "buy", "qty": "5"},
            }
        )
        assert payload.event_kind == "proposal"  # type: ignore[union-attr]

    def test_approval_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "approval",
                "proposal_id": "p1",
                "actor": "U1234",
                "slack_action_id": "act_5678",
            }
        )
        assert payload.event_kind == "approval"  # type: ignore[union-attr]

    def test_rejection_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "rejection",
                "proposal_id": "p1",
                "actor": "U1234",
                "reason": "no_thanks",
            }
        )
        assert payload.event_kind == "rejection"  # type: ignore[union-attr]

    def test_order_submitted_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "order_submitted",
                "client_order_id": "a" * 32,
                "broker_order_id": "alp-xyz",
                "symbol": "NVDA",
                "side": "buy",
                "qty": "5",
                "order_type": "limit",
            }
        )
        assert payload.event_kind == "order_submitted"  # type: ignore[union-attr]

    def test_fill_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "fill",
                "client_order_id": "a" * 32,
                "broker_order_id": "alp-xyz",
                "filled_qty": "5",
                "filled_avg_price": "1234.56",
                "ticker": "NVDA",
            }
        )
        assert payload.event_kind == "fill"  # type: ignore[union-attr]

    def test_error_event_validates(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        payload = adapter.validate_python(
            {
                "event_kind": "error",
                "context": "executor",
                "error_class": "BrokerOrderError",
                "error_message": "duplicate client_order_id",
            }
        )
        assert payload.event_kind == "error"  # type: ignore[union-attr]

    def test_unknown_event_kind_rejected(self) -> None:
        from gekko.schemas.event import EventPayload

        adapter = TypeAdapter(EventPayload)
        with pytest.raises(ValidationError):
            adapter.validate_python(
                {"event_kind": "lunch_break", "anything": "goes"}
            )


# ---------------------------------------------------------------------------
# Rationale headroom (quick 260612-dix)
# ---------------------------------------------------------------------------
#
# Plan 01-09 Task 5 walking-skeleton demo exposed Sonnet emitting realistic
# ~1200-3500-char trade rationales that tripped the prior max_length=1000
# guard. Anthropic's tool-use docs say JSON Schema maxLength is a soft hint —
# we cannot rely on the LLM to self-cap, so the schema now permits up to 5000
# chars and Slack rendering is bounded separately via _truncate_for_slack
# (see test_slack_block_kit.py).


def test_trade_proposal_rationale_accepts_4999_chars() -> None:
    """5000-char cap leaves headroom for realistic Sonnet rationales (1-shot dix)."""
    from gekko.schemas.proposal import TradeProposal

    tp = TradeProposal(**_trade_proposal_kwargs(rationale="x" * 4999))  # type: ignore[arg-type]
    assert len(tp.rationale) == 4999


def test_trade_proposal_rationale_rejects_5001_chars() -> None:
    """Above the 5000-char cap, Pydantic raises ``string_too_long`` (1-shot dix)."""
    from gekko.schemas.proposal import TradeProposal

    with pytest.raises(ValidationError) as exc_info:
        TradeProposal(**_trade_proposal_kwargs(rationale="x" * 5001))  # type: ignore[arg-type]
    assert "string_too_long" in str(exc_info.value)


def test_no_action_rationale_accepts_4999_chars() -> None:
    """NoActionProposal mirrors the TradeProposal cap for D-09 verbose drafts."""
    from gekko.schemas.proposal import NoActionProposal

    n = NoActionProposal(
        user_id="alice",
        strategy_name="ai-infra",
        decision_id="d1",
        rationale="x" * 4999,
        factors_considered=["price_vs_thesis"],
        confidence=Decimal("0.5"),
    )
    assert len(n.rationale) == 4999


def test_no_action_rationale_rejects_5001_chars() -> None:
    """NoActionProposal above 5000 chars is rejected with ``string_too_long``."""
    from gekko.schemas.proposal import NoActionProposal

    with pytest.raises(ValidationError) as exc_info:
        NoActionProposal(
            user_id="alice",
            strategy_name="ai-infra",
            decision_id="d1",
            rationale="x" * 5001,
            factors_considered=["price_vs_thesis"],
            confidence=Decimal("0.5"),
        )
    assert "string_too_long" in str(exc_info.value)
