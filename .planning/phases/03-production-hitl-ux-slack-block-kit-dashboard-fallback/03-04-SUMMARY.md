---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 04
subsystem: hitl
tags: [apscheduler, slack, expiry, sweep, block-kit, ast-gate, tdd]

# Dependency graph
requires:
  - phase: 03-01
    provides: "expires_at column, EXPIRED state, expire_proposal helper, slack_message_ts/channel persistence (D-53)"
  - phase: 03-02
    provides: "claim_action dedup gate — sweep vs click race resolved by first-write-wins"
  - phase: 03-05
    provides: "build_proposal_card baseline card shape extended with expired=True branch"
provides:
  - "expire_stale_proposals(*, user_id) async sweep — expires PENDING proposals past expires_at within ~60s"
  - "register_expire_stale_sweep(scheduler, *, user_id) APScheduler registrar with restart-safe knobs"
  - "build_proposal_card(expired=True) greyed-out card variant — removes actions block, adds [EXPIRED] chip + context block"
  - "lifespan registers expiry sweep alongside Phase-1 daily jobs"
  - "test_expiry_no_sdk_import.py AST gate — sweep has zero claude_agent_sdk bytes"
  - "test_transition_status_callers.py AST caller-gate — every caller wraps transition_status in try/except ValueError"
  - "test_sweep_persistence.py — APScheduler restart-safety + coalesce + no-duplicate integration tests"
affects:
  - 03-06
  - 03-07

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "APScheduler restart-safe sweep: IntervalTrigger(seconds=60) + coalesce=True + max_instances=1 + misfire_grace_time=300"
    - "Module:fn string ref for APScheduler add_job (SQLAlchemyJobStore pickle-safe across restarts)"
    - "DB-first ordering: transition → append_event → commit → side-effects (chat.update + DM) outside transaction"
    - "Sweep-vs-click first-write-wins: transition_status raises ValueError when state already changed; sweep catches it"
    - "D-61 grandfathering: WHERE expires_at IS NOT NULL prevents pre-migration null rows from being swept"
    - "Lazy imports inside sweep to avoid circular import (reporter.slack, slack.app, execution.executor)"
    - "Patch lazy imports at source module, not at consumer module (e.g. gekko.slack.app.slack_app)"

key-files:
  created:
    - src/gekko/approval/expiry.py
    - tests/unit/test_expire_stale_proposals.py
    - tests/unit/test_expiry_no_sdk_import.py
    - tests/unit/test_chat_update_expired.py
    - tests/unit/test_transition_status_callers.py
    - tests/integration/test_sweep_persistence.py
  modified:
    - src/gekko/scheduler/jobs.py
    - src/gekko/dashboard/app.py
    - src/gekko/reporter/slack.py
    - src/gekko/dashboard/routes.py

key-decisions:
  - "Sweep calls transition_status then catches ValueError for sweep-vs-click race (D-53 first-write-wins)"
  - "D-61: WHERE expires_at IS NOT NULL guards grandfathered pre-migration rows from erroneous expiry"
  - "Lazy import chain (reporter.slack, slack.app, execution.executor) avoids circular dependency at module load"
  - "Expiry DMs use category=routine_fill (not bypass) so quiet hours still silence them"
  - "live_confirm_post Rule 2 fix: wrapped transition_status in try/except ValueError -> HTTPException(409)"

patterns-established:
  - "Sweep module is deterministic Python firewall: zero claude_agent_sdk or anthropic bytes (AST gate enforced in CI)"
  - "Every transition_status caller must wrap call in try/except ValueError (caller-gate AST test enforces this)"
  - "APScheduler sweeps registered after scheduler.start() so replace_existing dedupes against jobstore"

requirements-completed:
  - HITL-03

# Metrics
duration: 20min
completed: 2026-06-18
---

# Phase 03 Plan 04: Expiry Sweep (HITL-03) Summary

**60-second APScheduler sweep expires stale PENDING proposals via state-machine transition, chat.update to greyed-out Slack card, and operator DM — with coalesce restart safety and D-61 null grandfathering**

## Performance

- **Duration:** ~20 min (across context-compacted session)
- **Started:** 2026-06-18T02:59:24Z
- **Completed:** 2026-06-18T03:12:50Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments

- `expire_stale_proposals(*, user_id)` async sweep: SELECT WHERE expires_at <= now AND IS NOT NULL, per-row transition → audit event → commit → chat.update + DM side-effects outside transaction
- `build_proposal_card(expired=True)` extended: [EXPIRED] chip section, no actions block, context block with expiry status string, LIVE banner preserved
- APScheduler sweep registered in dashboard lifespan with module:fn string ref, coalesce=True, max_instances=1, misfire_grace_time=300 — survives gekko serve restarts without double-firing
- 19 tests pass (7 unit sweep, 1 AST byte-grep, 6 chat_update, 1 caller-gate AST, 4 integration persistence)

## Task Commits

Each task was committed atomically:

1. **Task 1: expire_stale_proposals sweep + register_expire_stale_sweep + AST gate** - `d87600d` (feat)
2. **Task 2: build_proposal_card expired=True + _chat_update_expired_card + caller-gate AST** - `48c7960` (feat)
3. **Task 3: lifespan registers expiry sweep + integration persistence tests** - `facbcb7` (feat)

## Files Created/Modified

- `src/gekko/approval/expiry.py` (NEW) - Core sweep: expire_stale_proposals, _chat_update_expired_card, _format_expiry_dm, _run_sweep. Deterministic Python firewall — zero claude_agent_sdk bytes.
- `src/gekko/scheduler/jobs.py` (MODIFIED) - Added register_expire_stale_sweep with IntervalTrigger(seconds=60) + restart-safe knobs; added to __all__
- `src/gekko/dashboard/app.py` (MODIFIED) - Lifespan calls register_expire_stale_sweep after scheduler.start()
- `src/gekko/reporter/slack.py` (MODIFIED) - build_proposal_card extended with expired bool, expired_at_local, timeout_minutes params; expired=True branch produces 8-block greyed card
- `src/gekko/dashboard/routes.py` (MODIFIED) - Rule 2 fix: live_confirm_post wraps transition_status in try/except ValueError -> HTTPException(409)
- `tests/unit/test_expire_stale_proposals.py` (NEW) - 7 sweep behavior tests including race swallowed, grandfathered null, double-sweep idempotent
- `tests/unit/test_expiry_no_sdk_import.py` (NEW) - AST gate: asserts no claude_agent_sdk or anthropic bytes in expiry.py
- `tests/unit/test_chat_update_expired.py` (NEW) - 6 tests: [EXPIRED] chip, no actions block, context status string, LIVE banner, chat_update args, missing-ts no-op
- `tests/unit/test_transition_status_callers.py` (NEW) - AST walk: every transition_status caller in src/gekko/ must wrap in try/except ValueError
- `tests/integration/test_sweep_persistence.py` (NEW) - 4 tests: restart persistence, no-duplicate on double-register, coalesce/max_instances/misfire_grace_time, module:fn string ref resolution

## Decisions Made

- **Lazy imports inside sweep**: `reporter.slack`, `slack.app`, `execution.executor` are imported inside `_run_sweep` to break circular import chains. Consequence: patches must target the source module, not the consumer.
- **Expiry DM uses category="routine_fill"**: Quiet hours will still silence expiry DMs (they're not urgent enough to bypass quiet hours).
- **Rule 2 fix on live_confirm_post**: The caller-gate AST test discovered that `live_confirm_post` in routes.py was calling `transition_status` without try/except ValueError. Fixed proactively since a sweep-vs-click race can also happen when a user clicks Approve on a proposal the sweep just expired.
- **D-61 grandfathering clause confirmed**: `WHERE expires_at IS NOT NULL` is enforced in the SELECT query to prevent proposals created before the migration from being erroneously swept.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Missing ValueError guard on live_confirm_post**
- **Found during:** Task 2 (test_transition_status_callers.py AST gate)
- **Issue:** `live_confirm_post` in `dashboard/routes.py` called `transition_status` without a `try/except ValueError`, meaning a sweep-vs-click race on the AWAITING_2ND_CHANNEL -> APPROVED_LIVE transition would crash with an unhandled 500 instead of returning a proper 409.
- **Fix:** Wrapped `transition_status` call in `try/except ValueError: raise HTTPException(status_code=409, detail=str(exc))`
- **Files modified:** `src/gekko/dashboard/routes.py`
- **Verification:** Caller-gate AST test passes; `live_confirm_post` is now in the exemption list with proper protection
- **Committed in:** `48c7960` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** The fix is essential for correctness under concurrent sweep + user-click races. No scope creep.

## Issues Encountered

- **EvidenceSnippet schema field names**: `source_type` enum values are restricted (`alpaca_quote`, `finnhub_news`, `edgar_filing`, `web_fetch`); the `url` field does not exist (correct field is `source_url`); `fetched_at` is required. Discovered during test_chat_update_expired.py fixture construction. Fixed by reading `src/gekko/schemas/research.py` directly.
- **AlternativeConsidered schema**: Does not have a `ticker` field; `description` is required. Fixed similarly.
- **Lazy import patching**: Patching `gekko.approval.expiry.build_proposal_card` fails because the function is lazily imported inside `_run_sweep` — must patch at `gekko.reporter.slack.build_proposal_card` (source module). Same for `slack_app`. This is a known lazy-import testing pattern; documented in PATTERNS.
- **APScheduler AsyncIOScheduler requires running event loop**: Tests that call `scheduler.start()` must be `async def` to have a running event loop. Fixed by converting all 4 integration test functions to async.

## Known Stubs

None — all test stubs from Task 1 (Wave 0) were fully populated.

## Threat Flags

None — expiry.py is a sweep-only module with no new network endpoints or auth paths. The chat.update call uses the existing slack_app.client (already in threat surface from Plan 03-01).

## Next Phase Readiness

- HITL-03 end-to-end is complete. Proposals that expire without operator action will now be automatically transitioned, cards greyed out, and operators notified.
- Plans 03-06 (email digest) and 03-07 (daily P&L) can proceed independently — they don't depend on the expiry sweep.
- No blockers.

## Self-Check: PASSED

Files created:
- FOUND: src/gekko/approval/expiry.py
- FOUND: src/gekko/scheduler/jobs.py (register_expire_stale_sweep present)
- FOUND: src/gekko/dashboard/app.py (register_expire_stale_sweep called in lifespan)
- FOUND: tests/unit/test_expire_stale_proposals.py
- FOUND: tests/unit/test_expiry_no_sdk_import.py
- FOUND: tests/unit/test_chat_update_expired.py
- FOUND: tests/unit/test_transition_status_callers.py
- FOUND: tests/integration/test_sweep_persistence.py

Commits verified: d87600d, 48c7960, facbcb7 (all present in git log)

Test run: 19 passed, 0 failed

---
*Phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback*
*Completed: 2026-06-18*
