"""Tests for ``gekko.schemas.strategy`` — Plan 01-06 Task 1.

The Pydantic contracts that Plans 01-07 / 01-08 / 01-09 all import. Each
test below maps to one bullet in the plan's ``<behavior>`` block.

References:
  * .planning/phases/01-foundation.../01-06-PLAN.md  (Task 1 behavior list)
  * .planning/phases/01-foundation.../01-CONTEXT.md  (D-01, D-02, D-05, D-08, STRAT-06)
  * .planning/phases/01-foundation.../01-RESEARCH.md (Strategy Pydantic example)
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# HardCaps
# ---------------------------------------------------------------------------


class TestHardCaps:
    """Tests for the ``HardCaps`` Pydantic model."""

    def test_valid_construction(self) -> None:
        from gekko.schemas.strategy import HardCaps

        caps = HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("200"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        )
        assert caps.max_position_pct == Decimal("0.05")
        assert caps.max_daily_loss_usd == Decimal("200")
        assert caps.max_trades_per_day == 3
        assert caps.max_sector_exposure_pct == Decimal("0.25")

    def test_max_position_pct_must_be_positive(self) -> None:
        from gekko.schemas.strategy import HardCaps

        with pytest.raises(ValidationError):
            HardCaps(
                max_position_pct=Decimal("0"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("0.25"),
            )

    def test_max_position_pct_defensive_ceiling(self) -> None:
        """Defensive ceiling: max_position_pct must be <= 0.20 (20%).

        Per RESEARCH §Code Examples — a strategy concentrating more than 20%
        in a single position is an architectural smell; the schema rejects.
        """
        from gekko.schemas.strategy import HardCaps

        with pytest.raises(ValidationError):
            HardCaps(
                max_position_pct=Decimal("0.99"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("0.25"),
            )

    def test_max_daily_loss_must_be_positive(self) -> None:
        from gekko.schemas.strategy import HardCaps

        with pytest.raises(ValidationError):
            HardCaps(
                max_position_pct=Decimal("0.05"),
                max_daily_loss_usd=Decimal("0"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("0.25"),
            )

    def test_max_trades_per_day_must_be_at_least_one(self) -> None:
        from gekko.schemas.strategy import HardCaps

        with pytest.raises(ValidationError):
            HardCaps(
                max_position_pct=Decimal("0.05"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=0,
                max_sector_exposure_pct=Decimal("0.25"),
            )

    def test_sector_exposure_in_unit_interval(self) -> None:
        from gekko.schemas.strategy import HardCaps

        with pytest.raises(ValidationError):
            HardCaps(
                max_position_pct=Decimal("0.05"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("1.5"),
            )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


def _valid_hard_caps_kwargs() -> dict[str, object]:
    return {
        "max_position_pct": Decimal("0.05"),
        "max_daily_loss_usd": Decimal("200"),
        "max_trades_per_day": 3,
        "max_sector_exposure_pct": Decimal("0.25"),
    }


def _valid_strategy_kwargs(**overrides: object) -> dict[str, object]:
    from gekko.schemas.strategy import HardCaps

    base: dict[str, object] = {
        "strategy_id": "strat-abc",
        "user_id": "alice",
        "name": "ai-infra",
        "version": 1,
        "thesis": "Bullish on AI infra; avoid Chinese names.",
        "watchlist": ["NVDA", "AMD"],
        "hard_caps": HardCaps(**_valid_hard_caps_kwargs()),  # type: ignore[arg-type]
        "schedule_time": None,
        "mode": "paper",
        "created_at": "2026-06-09T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestStrategy:
    """Tests for the ``Strategy`` Pydantic model."""

    def test_valid_construction(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs())  # type: ignore[arg-type]
        assert s.name == "ai-infra"
        assert s.version == 1
        assert s.mode == "paper"

    def test_json_roundtrip(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs())  # type: ignore[arg-type]
        payload = s.model_dump_json()
        reparsed = Strategy.model_validate_json(payload)
        assert reparsed == s

    def test_watchlist_uppercased_and_deduplicated(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(
            **_valid_strategy_kwargs(watchlist=["nvda", "NVDA", "amd"])  # type: ignore[arg-type]
        )
        # Upper + dedupe, preserve first-seen order.
        assert s.watchlist == ["NVDA", "AMD"]

    def test_watchlist_must_be_non_empty(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(**_valid_strategy_kwargs(watchlist=[]))  # type: ignore[arg-type]

    def test_mode_paper_accepted(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs(mode="paper"))  # type: ignore[arg-type]
        assert s.mode == "paper"

    def test_mode_live_accepted(self) -> None:
        """Schema accepts mode='live' — UI confirmation is enforced by Plan 01-09 dashboard."""
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs(mode="live"))  # type: ignore[arg-type]
        assert s.mode == "live"

    def test_mode_invalid_rejected(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(**_valid_strategy_kwargs(mode="margin"))  # type: ignore[arg-type]

    def test_mode_defaults_to_paper(self) -> None:
        from gekko.schemas.strategy import HardCaps, Strategy

        s = Strategy(
            strategy_id="strat-abc",
            user_id="alice",
            name="ai-infra",
            version=1,
            thesis="Bullish on AI infra.",
            watchlist=["NVDA"],
            hard_caps=HardCaps(**_valid_hard_caps_kwargs()),  # type: ignore[arg-type]
            created_at="2026-06-09T00:00:00+00:00",
        )
        assert s.mode == "paper"

    def test_schedule_time_accepts_valid_iana_tz(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(
            **_valid_strategy_kwargs(schedule_time="10:00 America/New_York")  # type: ignore[arg-type]
        )
        assert s.schedule_time == "10:00 America/New_York"

    def test_schedule_time_none_accepted(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs(schedule_time=None))  # type: ignore[arg-type]
        assert s.schedule_time is None

    def test_schedule_time_rejects_out_of_range_hour(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(
                **_valid_strategy_kwargs(schedule_time="25:00 America/New_York")  # type: ignore[arg-type]
            )

    def test_schedule_time_rejects_unknown_tz(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(
                **_valid_strategy_kwargs(schedule_time="10:00 NotATimezone")  # type: ignore[arg-type]
            )

    def test_thesis_max_length(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(**_valid_strategy_kwargs(thesis="x" * 5000))  # type: ignore[arg-type]

    def test_thesis_must_be_non_empty(self) -> None:
        from gekko.schemas.strategy import Strategy

        with pytest.raises(ValidationError):
            Strategy(**_valid_strategy_kwargs(thesis=""))  # type: ignore[arg-type]

    def test_created_by_chat_default_false(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs())  # type: ignore[arg-type]
        assert s.created_by_chat is False

    def test_created_by_chat_accepts_true(self) -> None:
        from gekko.schemas.strategy import Strategy

        s = Strategy(**_valid_strategy_kwargs(created_by_chat=True))  # type: ignore[arg-type]
        assert s.created_by_chat is True


# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------


class TestGuidance:
    """Tests for the ``Guidance`` Pydantic model (RES-08, STRAT-03)."""

    def test_valid_strategy_scoped(self) -> None:
        from gekko.schemas.strategy import Guidance

        g = Guidance(
            guidance_id="g1",
            user_id="alice",
            text="focus on energy this week",
            scope="strategy",
            strategy_id="strat-abc",
            created_at="2026-06-09T00:00:00+00:00",
        )
        assert g.scope == "strategy"
        assert g.strategy_id == "strat-abc"

    def test_valid_global_scope(self) -> None:
        from gekko.schemas.strategy import Guidance

        g = Guidance(
            guidance_id="g1",
            user_id="alice",
            text="avoid Chinese names",
            scope="global",
            created_at="2026-06-09T00:00:00+00:00",
        )
        assert g.scope == "global"
        assert g.strategy_id is None

    def test_invalid_scope_rejected(self) -> None:
        from gekko.schemas.strategy import Guidance

        with pytest.raises(ValidationError):
            Guidance(
                guidance_id="g1",
                user_id="alice",
                text="hello",
                scope="user",
                created_at="2026-06-09T00:00:00+00:00",
            )

    def test_expires_at_optional(self) -> None:
        from gekko.schemas.strategy import Guidance

        g = Guidance(
            guidance_id="g1",
            user_id="alice",
            text="focus on energy",
            scope="strategy",
            strategy_id="strat-abc",
            created_at="2026-06-09T00:00:00+00:00",
            expires_at="2026-06-16T00:00:00+00:00",
        )
        assert g.expires_at == "2026-06-16T00:00:00+00:00"
