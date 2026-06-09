"""Tests for ``gekko.schemas.diff`` — Plan 01-06 Task 1 (D-02 plain-English diff).

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-02 — plain-English diff
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Don't Hand-Roll" — deterministic for P1
"""

from __future__ import annotations

from decimal import Decimal


def _build_strategy(**overrides: object) -> object:
    from gekko.schemas.strategy import HardCaps, Strategy

    base = {
        "strategy_id": "strat-abc",
        "user_id": "alice",
        "name": "ai-infra",
        "version": 1,
        "thesis": "Bullish on AI infra.",
        "watchlist": ["NVDA", "AMD"],
        "hard_caps": HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("200"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        "schedule_time": None,
        "mode": "paper",
        "created_at": "2026-06-09T00:00:00+00:00",
    }
    base.update(overrides)
    return Strategy(**base)  # type: ignore[arg-type]


class TestComputeFieldChanges:
    def test_no_changes_returns_empty_dict(self) -> None:
        from gekko.schemas.diff import compute_field_changes

        s = _build_strategy()
        assert compute_field_changes(s, s) == {}

    def test_max_position_pct_change_detected(self) -> None:
        from gekko.schemas.diff import compute_field_changes
        from gekko.schemas.strategy import HardCaps

        before = _build_strategy()
        after = _build_strategy(
            hard_caps=HardCaps(
                max_position_pct=Decimal("0.07"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("0.25"),
            )
        )
        changes = compute_field_changes(before, after)
        assert "max_position_pct" in changes
        assert changes["max_position_pct"] == (Decimal("0.05"), Decimal("0.07"))

    def test_watchlist_add_and_remove(self) -> None:
        from gekko.schemas.diff import compute_field_changes

        before = _build_strategy(watchlist=["NVDA", "AMD"])
        after = _build_strategy(watchlist=["NVDA", "MSFT", "GOOGL"])
        changes = compute_field_changes(before, after)
        assert set(changes.get("watchlist_added", [])) == {"MSFT", "GOOGL"}
        assert set(changes.get("watchlist_removed", [])) == {"AMD"}

    def test_thesis_change_detected(self) -> None:
        from gekko.schemas.diff import compute_field_changes

        before = _build_strategy(thesis="Bullish on AI infra.")
        after = _build_strategy(thesis="Bullish on AI infra and energy.")
        changes = compute_field_changes(before, after)
        assert "thesis" in changes

    def test_schedule_time_added(self) -> None:
        from gekko.schemas.diff import compute_field_changes

        before = _build_strategy(schedule_time=None)
        after = _build_strategy(schedule_time="10:00 America/New_York")
        changes = compute_field_changes(before, after)
        assert "schedule_time" in changes


class TestGenerateStrategyDiff:
    def test_no_changes_returns_no_changes_sentence(self) -> None:
        from gekko.schemas.diff import generate_strategy_diff

        s = _build_strategy()
        assert generate_strategy_diff(s, s) == "No changes."

    def test_max_position_pct_in_diff(self) -> None:
        from gekko.schemas.diff import generate_strategy_diff
        from gekko.schemas.strategy import HardCaps

        before = _build_strategy()
        after = _build_strategy(
            hard_caps=HardCaps(
                max_position_pct=Decimal("0.07"),
                max_daily_loss_usd=Decimal("200"),
                max_trades_per_day=3,
                max_sector_exposure_pct=Decimal("0.25"),
            )
        )
        diff = generate_strategy_diff(before, after)
        # Per D-02: plain English with percent formatting.
        assert "5%" in diff
        assert "7%" in diff
        assert "max" in diff.lower() and "position" in diff.lower()

    def test_watchlist_add_in_diff(self) -> None:
        from gekko.schemas.diff import generate_strategy_diff

        before = _build_strategy(watchlist=["NVDA", "AMD"])
        after = _build_strategy(watchlist=["NVDA", "AMD", "MSFT", "GOOGL"])
        diff = generate_strategy_diff(before, after)
        assert "MSFT" in diff
        assert "GOOGL" in diff
        assert "added" in diff.lower()

    def test_thesis_edit_in_diff(self) -> None:
        from gekko.schemas.diff import generate_strategy_diff

        before = _build_strategy(thesis="Bullish on AI infra.")
        after = _build_strategy(thesis="Bullish on AI infra and energy.")
        diff = generate_strategy_diff(before, after)
        assert "thesis" in diff.lower()
        # The text per D-02 says "thesis edited" or similar — keep loose.
        assert "edit" in diff.lower() or "changed" in diff.lower()

    def test_schedule_time_added_in_diff(self) -> None:
        from gekko.schemas.diff import generate_strategy_diff

        before = _build_strategy(schedule_time=None)
        after = _build_strategy(schedule_time="10:00 America/New_York")
        diff = generate_strategy_diff(before, after)
        assert "10:00" in diff
        assert "America/New_York" in diff
