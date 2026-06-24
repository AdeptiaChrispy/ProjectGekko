---
phase: 04-agent-architecture-cost-bounds
plan: "08"
subsystem: agent/cost_ceiling
tags: [bug-fix, cost-ceiling, dedup, session-commit, regression-test]
dependency_graph:
  requires: ["04-07"]
  provides: ["committed-sent-date-writes", "cost-ceiling-dedup-regression-test"]
  affects: ["check_cost_ceiling", "cost_alert_80_sent_date", "cost_alert_100_sent_date"]
tech_stack:
  added: []
  patterns:
    - "async with session_factory() as session, session.begin(): — two-manager SQLAlchemy commit idiom"
key_files:
  created:
    - tests/integration/test_cost_ceiling_dedup.py
  modified:
    - src/gekko/agent/cost_ceiling.py
    - tests/unit/test_cost_ceiling.py
decisions:
  - "session.begin() wraps the entire check_cost_ceiling session block so the sent-date UPDATE is committed on normal exit and rolled back only on exception — mirrors proposal_writer.py idiom"
  - "Unit test mock factory updated with session.begin() async context-manager stub — keeps existing MagicMock tests passing with the two-manager form"
  - "Integration test uses strategy_id=None for llm_cost events (global events, no FK to strategies table required)"
metrics:
  duration: "~8 min"
  completed: "2026-06-24"
  tasks_completed: 2
  files_changed: 3
---

# Phase 04 Plan 08: Cost Ceiling Dedup Commit Fix Summary

**One-liner:** Committed `cost_alert_*_sent_date` writes via `session.begin()` in `check_cost_ceiling` to eliminate alert-DM spam (pre-fix `session.flush()` without `.begin()` discarded the UPDATE on every cycle).

## What Was Built

### Task 1: Fix `check_cost_ceiling` — commit the sent-date write

**File:** `src/gekko/agent/cost_ceiling.py` (line 132)

Single-line change: `async with session_factory() as session:` →
`async with session_factory() as session, session.begin():`

Root cause: `AsyncSession` opened without `.begin()` auto-begins a transaction that is **rolled back** on context-exit unless `session.commit()` or `session.begin()` is used. The existing `await session.flush()` at line 240 pushed the UPDATE to the DB cursor but the transaction was discarded before `check_cost_ceiling` returned — so the `cost_alert_80_sent_date` / `cost_alert_100_sent_date` columns appeared to change within the call but reverted to `None` in the DB. Every subsequent cycle saw `None` and re-fired the DM.

With `session.begin()`, the context manager commits on clean exit and rolls back on exception. The sent-date UPDATE persists. Second and subsequent calls in the same day see the stored date and skip the DM.

Also updated the module docstring to note the commit mechanism, and updated `_make_fake_session_factory` in `test_cost_ceiling.py` to add a `session.begin()` async context-manager stub so unit tests pass with the two-manager form.

### Task 2: Real-session regression test

**File:** `tests/integration/test_cost_ceiling_dedup.py`

Two `@pytest.mark.integration` tests that use a real SQLCipher engine:

- **`test_dedup_80_persists_across_sessions`**: Seeds user at 90% spend (0.09/0.10 ceiling), calls `check_cost_ceiling` twice. Asserts call 1 returns `just_crossed_80=True`; verifies `cost_alert_80_sent_date` is not None in a fresh read session; asserts call 2 returns `just_crossed_80=False`.
- **`test_dedup_100_persists_across_sessions`**: Same pattern for 100%/halt territory. Both `cost_alert_80_sent_date` and `cost_alert_100_sent_date` verified persisted; call 2 returns both flags `False`.

These tests fail against the pre-fix flush-only code and pass after the `session.begin()` commit fix.

## Operator Re-test Instructions

**No migration or schema change required.** This is a code-only fix.

1. Restart `gekko serve` to pick up the updated `cost_ceiling.py`.
2. In Settings, set the daily cost ceiling to a value just below the current day's accumulated spend.
3. Trigger two strategy cycles above the 80% threshold. Confirm exactly **one** 80% degrade DM arrives (not one per cycle).
4. Trigger spend past 100%. Confirm exactly **one** 100% halt DM arrives. Subsequent cycles that day produce no additional DMs.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Unit test mock missing `session.begin()` async context manager**

- **Found during:** Task 1 verification run
- **Issue:** `_make_fake_session_factory` in `test_cost_ceiling.py` built a `MagicMock` session with `flush` set but no `begin` attribute. The `AsyncMock` default for `begin()` returned a coroutine (not an async context manager), causing `TypeError: 'coroutine' object does not support the asynchronous context manager protocol`.
- **Fix:** Added `mock_begin_ctx = AsyncMock()` with `__aenter__`/`__aexit__` set, and assigned `mock_session.begin = MagicMock(return_value=mock_begin_ctx)`.
- **Files modified:** `tests/unit/test_cost_ceiling.py`
- **Commit:** `2ee1cd7` (bundled with the fix)

**2. [Rule 1 - Bug] Integration test used `strategy_id="s1"` which violates FK constraint**

- **Found during:** Task 2 first run
- **Issue:** `append_event(..., strategy_id="s1", ...)` caused `FOREIGN KEY constraint failed` because no Strategy row with id `"s1"` was seeded. `llm_cost` events are global (per-user pooled, D-01) and don't need a strategy FK.
- **Fix:** Changed to `strategy_id=None` — the `append_event` signature accepts `str | None` for exactly this case.
- **Files modified:** `tests/integration/test_cost_ceiling_dedup.py`
- **Commit:** `b5b4af8` (bundled with the test)

## Test Results

All targets green:

```
tests/unit/test_cost_ceiling.py              8 passed
tests/integration/test_cost_ceiling_dedup.py 2 passed
tests/unit/test_decision_prompt_isolation.py 4 passed
tests/unit/test_spend_route.py               6 passed
tests/unit/test_settings_route.py           10 passed
                                          ─────────
                                          30 passed
```

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `2ee1cd7` | fix | `session.begin()` commit in `check_cost_ceiling`; unit mock updated |
| `b5b4af8` | test | real-session dedup regression test for 80% and 100% paths |

## Self-Check: PASSED

- `src/gekko/agent/cost_ceiling.py` — FOUND
- `tests/integration/test_cost_ceiling_dedup.py` — FOUND
- Commit `2ee1cd7` — FOUND
- Commit `b5b4af8` — FOUND
