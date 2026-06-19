---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 12
subsystem: testing
tags: [executor, paper-trading, alpaca, market-hours, triage, broker]

# Dependency graph
requires:
  - phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
    provides: "approve_proposal_endpoint dispatches execute_proposal with user_id from require_session"
provides:
  - "Root cause of 'broker not configured' UAT observation documented: Scenario A (market closed)"
  - "Paper approve path confirmed working with monkeypatched broker — no BrokerOrderError on executor path"
  - "Architectural grep gate ensuring 'broker not configured' stays confined to alpaca_data.py"
affects:
  - 03-13 (HTMX polling — proposal status updates reflect correct FAILED/FILLED final state)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Triage-first: read source before writing fixes — prevents phantom bugs from becoming real code changes"
    - "Architectural grep gate: source-bytes assertion in test file catches future copy-paste of misleading error strings into wrong modules"

key-files:
  created: []
  modified:
    - tests/unit/test_executor.py

key-decisions:
  - "Scenario A confirmed: the 'broker not configured' log during UAT originated in alpaca_data.py (Researcher tool get_quote fallback when ctx.broker is None), not the executor path. The executor path has never contained that string."
  - "Root cause of FAILED proposal on UAT: is_market_open() returned False during off-hours testing. The market-closed guard (executor step 2) transitions APPROVED -> FAILED and sends a Slack DM — this is correct behavior, not a bug."
  - "No code fix needed in executor.py or routes.py — both are correctly wired. user_id from Depends(require_session) is settings.gekko_user_id, the correct value for executor DB scoping."
  - "Deliverable: two new tests prove (a) paper path reaches EXECUTING with monkeypatched broker when market is open, and (b) the misleading string is architecturally absent from executor and routes."

patterns-established: []

requirements-completed: []

# Metrics
duration: 30min
completed: 2026-06-19
---

# Phase 03 Plan 12: Triage — broker-not-configured failure on paper approve Summary

**Root cause diagnosed as Scenario A (market closed): 'broker not configured' log is the Researcher tool fallback in alpaca_data.py, never the executor path; paper approve works correctly with is_market_open=True.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-06-19T17:00:00Z
- **Completed:** 2026-06-19T17:27:00Z
- **Tasks:** 1 of 1
- **Files modified:** 1

## Diagnosis

### Root cause: Scenario A — market closed

The "broker not configured; falling back to yahooquery" message observed during UAT:

- **Source:** `src/gekko/agent/tools/alpaca_data.py` line 126 — the Researcher agent's `get_quote` tool when `ctx.broker is None`. This fires during strategy agent runs (research phase) when no broker is set in the tool context.
- **Not on the executor path:** `execute_proposal` in `executor.py` calls `_build_broker → AlpacaBroker → place_order`. It never calls `get_quote`. Confirmed with `grep -c "broker not configured" src/gekko/execution/executor.py` == 0.
- **The actual failure:** `is_market_open()` returned False during off-hours UAT testing. The executor's market-closed guard (step 2, line 481) transitions APPROVED → FAILED and sends a Slack DM explaining the deferral. The WR-02 fix (already landed in a prior plan) ensures the operator is notified via Slack.

### Triage findings

| Hypothesis | Status | Evidence |
|---|---|---|
| Scenario A: market closed | **CONFIRMED ROOT CAUSE** | is_market_open() returns False outside NYSE hours; executor transitions APPROVED→FAILED with correct Slack DM |
| Scenario B: user_id mismatch | Not the issue | require_session returns settings.gekko_user_id; executor uses same value for DB scope |
| Scenario C: empty alpaca_paper_api_key at construction | Not the issue | AlpacaBroker raises BrokerConfigError (different message), settings validated at startup |
| "broker not configured" in executor.py | **Architectural assertion: ABSENT** | grep returns 0 — string is confined to alpaca_data.py |

### No code fix required

`executor.py` and `routes.py` are correctly wired. The approve endpoint dispatches `execute_proposal(proposal_id, user_id)` with the correct `user_id` from the authenticated session. The executor constructs `AlpacaBroker(paper=True)` from settings credentials. The paper path succeeds when the market is open.

## Accomplishments

- Triaged all three hypotheses; confirmed Scenario A as the root cause
- Confirmed "broker not configured" is architecturally absent from the executor path (grep gate)
- Added test proving paper approve path reaches EXECUTING with monkeypatched broker and is_market_open=True
- Added architectural grep gate test to prevent future copy-paste of the misleading string into executor/routes
- All 13 executor unit tests pass

## Task Commits

1. **Task 1: Triage + paper-path test** — `8127634` (test)

## Files Created/Modified

- `tests/unit/test_executor.py` — Added 2 new tests:
  - `test_paper_approve_path_executes_without_broker_not_configured_error`: paper APPROVED → EXECUTING with monkeypatched broker, no BrokerOrderError raised
  - `test_broker_not_configured_string_absent_from_executor_source`: architectural grep gate on executor.py and routes.py source bytes

## Decisions Made

- No code change to executor.py or routes.py — triage confirmed no wiring gap exists
- Scenario A (market closed) is the authoritative root cause; documented as decision for STATE.md
- Operator behavior: when approving during market hours, the paper path will succeed; during off-hours, the proposal goes FAILED with a Slack DM notification (correct behavior per EXEC-10 / Plan 01-08)

## Deviations from Plan

None — plan executed exactly as written. Scenario A was the plan's own primary hypothesis; triage confirmed it.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- Paper approve path confirmed correct — unblocks DASH-04 / SC-5 for market-hours testing
- Plan 03-13 (HTMX polling for status updates) can proceed; the executor correctly transitions proposals to FILLED or FAILED with audit events that polling will surface

---
*Phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback*
*Completed: 2026-06-19*
