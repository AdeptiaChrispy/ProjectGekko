---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: "09"
subsystem: reporting, audit, expiry
tags: [daily-pnl, audit-log, quiet-hours, expiry, slack-dm, executor, fill-event]

requires:
  - phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
    provides: on_fill_event executor, _send_dm_blocks_respecting_quiet_hours, expire_stale_proposals

provides:
  - fill audit events include strategy_name and side from tp_persisted (CR-02 fixed)
  - daily_pnl audit event records delivered and suppressed_by_quiet_hours fields (CR-03 fixed)
  - expiry DM uses executor_error category (non-suppressible, CR-04 fixed)
  - _send_dm_blocks_respecting_quiet_hours returns bool

affects:
  - 03-10-PLAN (final gap-closure plan in phase 03)
  - daily P&L reporting correctness
  - audit log integrity
  - trade expiry signal reliability

tech-stack:
  added: []
  patterns:
    - "Audit honesty: functions that may suppress side effects return bool so callers can record actual status"
    - "Bypass category for critical operator signals: executor_error bypasses quiet hours; use it for trade expiry"
    - "TDD fill-payload test pattern: seed proposal with 32-char client_order_id (TradeProposal min_length constraint)"

key-files:
  created:
    - tests/unit/test_fill_payload_fields.py
    - tests/unit/test_daily_pnl_audit_honesty.py
    - tests/unit/test_expiry_quiet_hours_bypass.py
  modified:
    - src/gekko/execution/executor.py
    - src/gekko/reporter/daily_pnl.py
    - src/gekko/approval/expiry.py

key-decisions:
  - "CR-02: strategy_name and side added to fill_payload dict in on_fill_event from tp_persisted; defensive fallback '' when tp_persisted is None (malformed payload_json)"
  - "CR-03: _send_dm_blocks_respecting_quiet_hours returns bool; send_daily_pnl_digest captures dispatched bool and writes delivered + suppressed_by_quiet_hours to audit event; event is always written (suppressed or not) for complete audit trail"
  - "CR-04: expiry DM changed from routine_fill to executor_error category; proposal expiry is a dropped real-money decision requiring non-suppressible delivery per D-48"
  - "Monkeypatch target for quiet-hours tests: gekko.approval.quiet_hours._resolve_quiet_hours (patch-where-defined; deferred local import means patching at module scope of daily_pnl.py would not intercept)"

patterns-established:
  - "Side-effect bool return: quiet-hours wrappers now return bool so callers can write honest audit events"
  - "executor_error bypass category for trade lifecycle events: expiry is now classified as an executor-level error signal, not routine"

requirements-completed:
  - REPT-01
  - HITL-03

duration: 25min
completed: 2026-06-18
---

# Phase 03 Plan 09: Gap-Closure CR-02/CR-03/CR-04 Summary

**Three surgical correctness fixes: fill events now carry strategy_name + side for accurate P&L bucketing, daily P&L audit events reflect actual DM delivery status, and expiry DMs bypass quiet hours via executor_error category.**

## Performance

- **Duration:** 25 min
- **Started:** 2026-06-18T12:45:00Z
- **Completed:** 2026-06-18T13:10:00Z
- **Tasks:** 2
- **Files modified:** 6 (3 source, 3 test)

## Accomplishments

- CR-02: on_fill_event fill_payload now includes strategy_name (from tp_persisted.strategy_name) and side (from str(tp_persisted.side).lower()), fixing per-strategy P&L bucketing and SELL sign convention in the daily digest
- CR-03: _send_dm_blocks_respecting_quiet_hours returns bool; send_daily_pnl_digest captures the return value and writes delivered + suppressed_by_quiet_hours to the daily_pnl audit event, ensuring the audit log reflects reality instead of always claiming the DM was sent
- CR-04: expiry DM category changed from routine_fill (suppressible) to executor_error (D-48 bypass), guaranteeing operator notification even when a proposal expires during the quiet window

## Task Commits

1. **Task 1: Add strategy_name and side to fill_payload in on_fill_event (CR-02)** - `b386a47` (feat)
2. **Task 2: Fix daily_pnl audit honesty (CR-03) and expiry quiet-hours bypass (CR-04)** - `6c493dd` (fix)

## Files Created/Modified

- `src/gekko/execution/executor.py` - fill_payload dict extended with strategy_name and side keys
- `src/gekko/reporter/daily_pnl.py` - _send_dm_blocks_respecting_quiet_hours returns bool; send_daily_pnl_digest captures dispatched and writes delivered/suppressed_by_quiet_hours to audit event
- `src/gekko/approval/expiry.py` - expiry DM category changed to executor_error with updated comment explaining D-48 bypass rationale
- `tests/unit/test_fill_payload_fields.py` - 4 TDD tests for CR-02 (strategy_name/side in fill payload; SELL positive P&L; _unknown_ fallback)
- `tests/unit/test_daily_pnl_audit_honesty.py` - 4 tests for CR-03 (audit event delivered/suppressed_by_quiet_hours fields; bool return from _send_dm_blocks_respecting_quiet_hours)
- `tests/unit/test_expiry_quiet_hours_bypass.py` - 2 tests for CR-04 (expiry DM fires with executor_error category during and outside quiet hours)

## Decisions Made

- Used `str(tp_persisted.side).lower()` for side normalization to handle both OrderSide enum and string variants without breaking the aggregator's `side == "sell"` comparison
- Audit event is always written regardless of DM delivery outcome (suppressed or sent) to maintain a complete audit trail; only the delivered/suppressed_by_quiet_hours fields change
- Chose executor_error (not first_live_fill or kill_active) for expiry bypass because expiry represents an executor-level failure state that must always reach the operator

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test helper used short client_order_id failing TradeProposal validation**
- **Found during:** Task 1 (test_on_fill_event_includes_strategy_name_and_side)
- **Issue:** `_seed_user_strategy_and_proposal` generated `client_order_id = f"coid-{uuid4().hex[:8]}"` (~13 chars) but TradeProposal schema requires `min_length=32`; model_validate_json silently failed, making tp_persisted=None and writing empty strategy_name/side to fill_payload — test would never catch the real bug
- **Fix:** Changed to `client_order_id = uuid4().hex` (exactly 32-char hex string)
- **Files modified:** tests/unit/test_fill_payload_fields.py
- **Verification:** Tests pass with correct strategy_name='momentum' in fill payload
- **Committed in:** b386a47 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 3 — blocking test infrastructure issue)
**Impact on plan:** Fix required for tests to actually validate the CR-02 change. Without it, tests would have passed trivially on empty defaults — exactly the scenario the plan specified must not happen.

## Issues Encountered

- Integration test `test_live_confirm_idempotent.py::test_live_confirm_double_post_is_idempotent` fails pre-existing (verified by reverting all changes and running; failure exists on committed HEAD). Not caused by this plan.

## Known Stubs

None - all changes are correctness fixes that flow actual data (strategy_name, side, delivered, suppressed_by_quiet_hours) through the system.

## Threat Flags

None - no new network endpoints, auth paths, or schema changes at trust boundaries. Changes are internal to existing audit event payloads (encrypted per-user SQLCipher DB per T-03-09-01).

## Self-Check: PASSED

Files verified:
- src/gekko/execution/executor.py — FOUND (strategy_name in fill_payload at line ~864)
- src/gekko/reporter/daily_pnl.py — FOUND (dispatched at line ~422, delivered/suppressed_by_quiet_hours at lines ~446-447)
- src/gekko/approval/expiry.py — FOUND (executor_error at line ~386)
- tests/unit/test_fill_payload_fields.py — FOUND (4 tests)
- tests/unit/test_daily_pnl_audit_honesty.py — FOUND (4 tests)
- tests/unit/test_expiry_quiet_hours_bypass.py — FOUND (2 tests)

Commits verified:
- b386a47 — feat(03-09): CR-02 — add strategy_name and side to on_fill_event fill_payload
- 6c493dd — fix(03-09): CR-03 + CR-04 — audit honesty and expiry quiet-hours bypass

## Next Phase Readiness

- Plan 03-10 (final gap-closure plan) can proceed; CR-02/CR-03/CR-04 are closed
- The daily P&L digest will now show correct per-strategy buckets and correct SELL sign after the next trading day's fills
- Audit logs are now trustworthy for digest delivery history reconstruction

---
*Phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback*
*Completed: 2026-06-18*
