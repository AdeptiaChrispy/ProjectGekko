---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 03
subsystem: quiet-hours-hitl
tags: [quiet-hours, hitl, scheduler, dst, zoneinfo, bypass-categories, ast-gate]
dependency_graph:
  requires:
    - 03-01  # schema substrate (User.quiet_hours_* + User.timezone columns)
  provides:
    - _resolve_quiet_hours-predicate
    - _send_slack_dm_respecting_quiet_hours-wrapper
    - trigger_strategy_run-quiet-hours-gate
    - ast-gate-executor-dm-callsites
  affects:
    - src/gekko/approval/quiet_hours.py
    - src/gekko/execution/executor.py
    - src/gekko/agent/runtime.py
    - tests/unit/test_quiet_hours_predicate.py
    - tests/unit/test_dm_bypass_categories.py
    - tests/unit/test_dm_routine_suppressed.py
    - tests/unit/test_quiet_hours_dm_gate.py
    - tests/integration/test_scheduler_quiet_hours.py
tech_stack:
  added: []
  patterns:
    - _get_session_factory shim (PATTERNS §2d) in quiet_hours.py
    - Lazy import inside function to avoid circular imports (quiet_hours.py → executor.py and runtime.py)
    - AST NodeVisitor gate scanning within 5 preceding lines for bypass-category annotation
    - zoneinfo-based DST-safe TZ arithmetic (PATTERNS §2h)
    - Overnight-wrap window comparison (start > end)
key_files:
  created:
    - src/gekko/approval/quiet_hours.py
    - tests/unit/test_quiet_hours_predicate.py
    - tests/unit/test_dm_bypass_categories.py
    - tests/unit/test_dm_routine_suppressed.py
    - tests/unit/test_quiet_hours_dm_gate.py
    - tests/integration/test_scheduler_quiet_hours.py
  modified:
    - src/gekko/execution/executor.py
    - src/gekko/agent/runtime.py
decisions:
  - "_resolve_quiet_hours loads User+Strategy rows then disposes engine BEFORE tz arithmetic — avoids holding DB connection during ZoneInfo computation"
  - "AST gate looks back 5 lines (not 1) for bypass-category annotation to accommodate multi-line try: blocks"
  - "bypass-category annotation also added inside _send_slack_dm_respecting_quiet_hours itself — the wrapper's own _send_slack_dm calls need annotation for the gate to pass"
  - "first_live_fill category determined by live_strategy_name_to_stamp non-None — mirrors the existing LIVE-fill detection logic; paper fills use routine_fill"
  - "strategy override resolution uses payload_json JSON parse — Strategy.quiet_hours_* fields live in payload_json (Pydantic), not as ORM columns"
  - "Manual source check is source != 'schedule' — any non-schedule source (manual, cli, slack, dashboard) bypasses per D-46"
metrics:
  duration_minutes: 16
  completed: "2026-06-18"
  tasks_completed: 3
  files_modified: 2
  files_created: 6
---

# Phase 3 Plan 3: Quiet Hours Predicate + DM Routing + Scheduler Gate Summary

**One-liner:** IANA-timezone-aware quiet-hours predicate with strategy-override precedence, bypass-category DM routing wrapper, APScheduler skip gate, and AST enforcement that every `_send_slack_dm` call site is classified.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 — _resolve_quiet_hours predicate | 6932701 | New quiet_hours.py; IANA tz via zoneinfo; overnight-wrap; strategy override wins (D-47); DST spring-forward + fall-back tests; 10 unit tests |
| 2 — _send_slack_dm_respecting_quiet_hours + AST gate | fe597d4 | Wrapper in executor.py; bypass set (kill_active, executor_error, first_live_fill); on_fill_event rewired; bypass annotations on all direct DM calls; AST gate (2 checks, 7 unit tests) |
| 3 — trigger_strategy_run quiet-hours gate + integration tests | 3e80ba1 | Gate at top of trigger_strategy_run for source="schedule"; manual bypass per D-46; 4 integration tests (schedule×in/out, manual×in/out) |

## What Was Built

### `src/gekko/approval/quiet_hours.py` (NEW)

- `_resolve_quiet_hours(user_id, now, strategy_name=None) -> bool` async predicate
- Module-local `_get_session_factory` shim per PATTERNS §2d
- IANA timezone resolution via `ZoneInfo`; raises `ValueError` for invalid tz (T-03-03-01)
- Overnight-wrap detection (`start > end`): in-window iff `local_time >= start OR local_time < end`
- Same-day window: in-window iff `start <= local_time < end`
- Strategy override precedence (D-47): strategy wins when BOTH `quiet_hours_start` AND `quiet_hours_end` are non-null in `payload_json`; half-set falls back to user window
- No Agent SDK or LLM provider imports (deterministic Python firewall)

### `src/gekko/execution/executor.py` (EXTENDED)

- New `_send_slack_dm_respecting_quiet_hours(user_id, text, *, category)` wrapper
- Bypass categories (always fire): `kill_active`, `executor_error`, `first_live_fill`
- Routine categories (suppressible): `routine_fill`, `daily_pnl`
- `on_fill_event` fill DM rewired: LIVE fills → `first_live_fill`, paper fills → `routine_fill`
- `bypass-category` annotations added above every existing direct `_send_slack_dm` call
- Lazy import of `_resolve_quiet_hours` inside wrapper function (avoids circular import)
- Fail-open: if predicate raises, logs exception and sends the DM anyway

### `src/gekko/agent/runtime.py` (EXTENDED)

- Quiet-hours gate at top of `trigger_strategy_run` body (after logging, before engine/session setup)
- Only fires when `source == "schedule"` (D-46 manual-override semantics)
- Returns `{outcome: "skipped_quiet_hours", ...}` immediately when in-window
- Lazy import of `_resolve_quiet_hours` inside the if-block

### AST Gate (`tests/unit/test_quiet_hours_dm_gate.py`)

- **Gate 1**: Every `_send_slack_dm` call in `executor.py` must have a `# bypass-category: <name>` comment within 5 preceding lines
- **Gate 2**: Every `_send_slack_dm_respecting_quiet_hours` call must pass `category=` keyword argument
- Future contributors cannot add an unclassified direct DM call without failing CI

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] AST gate "look-back = 1" too narrow for try: block patterns**
- **Found during:** Task 2 first test run
- **Issue:** The annotation comment is placed before `try:` → `await _send_slack_dm(...)` inside the try block; the 1-line look-back only saw `try:`, not the comment
- **Fix:** Extended look-back to 5 lines in the AST gate test; added bypass annotations directly before the calls inside the wrapper function body
- **Files modified:** `tests/unit/test_quiet_hours_dm_gate.py`
- **Commit:** fe597d4 (same task)

**2. [Rule 1 - Bug] ResearchBrief missing required fields in integration test helper**
- **Found during:** Task 3 first test run
- **Issue:** `ResearchBrief` requires `user_id`, `run_id`, `generated_at`; the helper was missing them (schema changed between Plan 01-06 initial scaffold and production)
- **Fix:** Updated `_build_fake_researcher_result()` with the required fields
- **Files modified:** `tests/integration/test_scheduler_quiet_hours.py`
- **Commit:** 3e80ba1 (same task)

**3. [Rule 1 - Bug] Wrong outcome assertion in integration test**
- **Found during:** Task 3 second test run
- **Issue:** Asserted `outcome == "trade"` but `trigger_strategy_run` returns `outcome = tool_outcome` which is `"propose_trade"`
- **Fix:** Changed assertions to `"propose_trade"`
- **Files modified:** `tests/integration/test_scheduler_quiet_hours.py`
- **Commit:** 3e80ba1 (same task)

**4. [Rule 1 - Bug] `write_proposal` patched at wrong module path**
- **Found during:** Task 3 third test run
- **Issue:** Patched `gekko.agent.proposal_writer.write_proposal` but `runtime.py` imports it directly (`from gekko.agent.proposal_writer import write_proposal`); the patch must target the import site
- **Fix:** Changed patch target to `gekko.agent.runtime.write_proposal`
- **Files modified:** `tests/integration/test_scheduler_quiet_hours.py`
- **Commit:** 3e80ba1 (same task)

**5. [Rule 2 - Missing critical] Strategy payload_json seeded as "{}" breaks load_latest_strategy**
- **Found during:** Task 3 second test run
- **Issue:** `_seed_user_and_strategy` used `payload_json="{}"` but `load_latest_strategy` calls `Strategy.model_validate_json(row.payload_json)` which requires all required fields
- **Fix:** Added `_make_strategy()` helper that builds a full `Strategy` Pydantic instance; seed with `strategy.model_dump_json()`
- **Files modified:** `tests/integration/test_scheduler_quiet_hours.py`
- **Commit:** 3e80ba1 (same task)

## Known Stubs

No new stubs introduced. The 3 test files from Wave 0 stubs (test_quiet_hours_predicate.py, test_dm_bypass_categories.py, test_dm_routine_suppressed.py, test_quiet_hours_dm_gate.py, test_scheduler_quiet_hours.py) are now fully populated.

## Threat Surface Scan

No new network endpoints or auth paths introduced. `quiet_hours.py` reads via the existing `_get_session_factory` shim. No new threat flags.

## Self-Check

Files created/modified verified:
- `src/gekko/approval/quiet_hours.py` — exists, contains `async def _resolve_quiet_hours`
- `src/gekko/execution/executor.py` — exists, contains `_send_slack_dm_respecting_quiet_hours`
- `src/gekko/agent/runtime.py` — exists, contains `_resolve_quiet_hours` and `agent.cycle.skipped_quiet_hours`
- `tests/unit/test_quiet_hours_predicate.py` — exists, 10 tests
- `tests/unit/test_dm_bypass_categories.py` — exists, 3 tests
- `tests/unit/test_dm_routine_suppressed.py` — exists, 2 tests
- `tests/unit/test_quiet_hours_dm_gate.py` — exists, 2 tests
- `tests/integration/test_scheduler_quiet_hours.py` — exists, 4 tests

Commits verified:
- 6932701 — Task 1
- fe597d4 — Task 2
- 3e80ba1 — Task 3

22/22 tests pass (10 unit predicate + 3 bypass + 2 routine + 2 AST gate + 4 integration + 1 P1 walking-skeleton).

## Self-Check: PASSED
