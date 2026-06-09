"""Tests for ``gekko.agent.budget.BudgetTracker`` — Plan 01-07 Task 2.

Per CONTEXT D-13 and RESEARCH §Pattern 1: per-cycle research budget is
soft + 2x grace. Soft threshold logs a warning; 2x threshold raises
:class:`gekko.core.errors.BudgetExceeded`.

The 10 behaviors per the plan:

1. ``BudgetTracker()`` defaults: soft_max_calls=12, soft_max_tokens=8000,
   soft_max_seconds=60.
2. ``record_call(tokens=100)`` at call 1 does NOT raise and does NOT warn.
3. After 13 calls (1 past soft), no exception (still under 2x).
4. After 25 calls (>2x soft of 12), the 25th ``record_call`` raises
   ``BudgetExceeded``.
5. Tokens exceeding 2x (16001+) raises ``BudgetExceeded``.
6. Elapsed wall time exceeding 2x (>120s) raises ``BudgetExceeded``.
7. ``BudgetExceeded`` message includes the offending counter values.
8. ``record_call`` emits a structlog ``warning`` event named
   ``research.budget.soft_exceeded`` at the soft threshold.
9. ``BudgetTracker`` is a dataclass with serializable state — ``to_dict``
   returns ``{"calls", "tokens", "seconds"}``.
10. Custom soft caps accepted: ``BudgetTracker(soft_max_calls=5)`` raises
    on the 11th call (>2x 5).
"""

from __future__ import annotations

import pytest
import structlog


def test_default_soft_caps() -> None:
    """Behavior 1: defaults match D-13 (12 calls / 8000 tokens / 60s)."""
    from gekko.agent.budget import BudgetTracker

    tracker = BudgetTracker()
    assert tracker.soft_max_calls == 12
    assert tracker.soft_max_tokens == 8000
    assert tracker.soft_max_seconds == 60.0


def test_first_call_no_raise_no_warn() -> None:
    """Behavior 2: call 1 with small token cost does NOT raise/warn."""
    from gekko.agent.budget import BudgetTracker

    tracker = BudgetTracker()
    with structlog.testing.capture_logs() as logs:
        tracker.record_call(tokens=100)
    soft_warnings = [
        entry for entry in logs if entry.get("event") == "research.budget.soft_exceeded"
    ]
    assert soft_warnings == []
    assert tracker.calls == 1
    assert tracker.tokens_used == 100


def test_thirteen_calls_no_exception() -> None:
    """Behavior 3: 13 calls (1 past soft=12) does NOT raise — still under 2x=24."""
    from gekko.agent.budget import BudgetTracker

    tracker = BudgetTracker()
    for _ in range(13):
        tracker.record_call(tokens=0)
    # Did not raise — only the soft warning fired.
    assert tracker.calls == 13


def test_twenty_fifth_call_raises_budget_exceeded() -> None:
    """Behavior 4: the 25th ``record_call`` raises (>2x 12=24)."""
    from gekko.agent.budget import BudgetTracker
    from gekko.core.errors import BudgetExceeded

    tracker = BudgetTracker()
    # Calls 1..24 must NOT raise.
    for _ in range(24):
        tracker.record_call(tokens=0)
    # Call 25 — first call where calls > 2x soft_max_calls (24).
    with pytest.raises(BudgetExceeded):
        tracker.record_call(tokens=0)


def test_tokens_exceeding_2x_raises() -> None:
    """Behavior 5: tokens > 2x soft (16000) raises ``BudgetExceeded``."""
    from gekko.agent.budget import BudgetTracker
    from gekko.core.errors import BudgetExceeded

    tracker = BudgetTracker()
    # 16001 tokens in one call — calls=1 (under), tokens=16001 (>2x of 8000).
    with pytest.raises(BudgetExceeded):
        tracker.record_call(tokens=16001)


def test_elapsed_seconds_exceeding_2x_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavior 6: elapsed > 2x soft_max_seconds (120s) raises.

    We monkeypatch ``time.monotonic`` inside the ``gekko.agent.budget``
    module so the tracker's ``started_at`` references our fake clock too.
    """
    from gekko.agent import budget as budget_mod
    from gekko.core.errors import BudgetExceeded

    fake_clock = {"now": 1000.0}

    def fake_monotonic() -> float:
        return fake_clock["now"]

    monkeypatch.setattr(budget_mod.time, "monotonic", fake_monotonic)

    tracker = budget_mod.BudgetTracker()
    # Advance the fake clock past 2x 60s = 120s after construction.
    fake_clock["now"] += 121.0
    with pytest.raises(BudgetExceeded):
        tracker.record_call(tokens=0)


def test_exception_message_includes_counter_values() -> None:
    """Behavior 7: BudgetExceeded message includes calls/tokens/seconds values."""
    from gekko.agent.budget import BudgetTracker
    from gekko.core.errors import BudgetExceeded

    tracker = BudgetTracker()
    # Push tokens hard so the message reflects a recognizable number.
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.record_call(tokens=50000)
    msg = str(excinfo.value)
    assert "calls=" in msg
    assert "tokens=50000" in msg
    assert "seconds=" in msg


def test_soft_warning_logged_at_threshold() -> None:
    """Behavior 8: structlog ``research.budget.soft_exceeded`` fires at soft threshold."""
    from gekko.agent.budget import BudgetTracker

    tracker = BudgetTracker()
    # First 12 calls — at or below soft (12). 13th call crosses soft (>12).
    for _ in range(12):
        tracker.record_call(tokens=0)
    with structlog.testing.capture_logs() as logs:
        tracker.record_call(tokens=0)
    soft_warnings = [
        entry for entry in logs if entry.get("event") == "research.budget.soft_exceeded"
    ]
    assert len(soft_warnings) == 1
    entry = soft_warnings[0]
    assert entry.get("log_level") == "warning"
    assert entry.get("calls") == 13


def test_to_dict_serializable_state() -> None:
    """Behavior 9: ``to_dict`` returns {calls, tokens, seconds} for ResearchBrief."""
    from gekko.agent.budget import BudgetTracker

    tracker = BudgetTracker()
    tracker.record_call(tokens=250)
    tracker.record_call(tokens=375)
    snapshot = tracker.to_dict()
    assert set(snapshot.keys()) == {"calls", "tokens", "seconds"}
    assert snapshot["calls"] == 2
    assert snapshot["tokens"] == 625
    assert isinstance(snapshot["seconds"], float)
    assert snapshot["seconds"] >= 0.0


def test_custom_soft_caps_accepted() -> None:
    """Behavior 10: ``BudgetTracker(soft_max_calls=5)`` raises on the 11th call."""
    from gekko.agent.budget import BudgetTracker
    from gekko.core.errors import BudgetExceeded

    tracker = BudgetTracker(soft_max_calls=5)
    # Calls 1..10 must NOT raise (under 2x=10 inclusive). Call 11 raises (>10).
    for _ in range(10):
        tracker.record_call(tokens=0)
    with pytest.raises(BudgetExceeded):
        tracker.record_call(tokens=0)
