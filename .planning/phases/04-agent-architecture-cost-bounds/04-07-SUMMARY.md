---
phase: 04-agent-architecture-cost-bounds
plan: "07"
subsystem: database
tags: [alembic, migration, sqlite, decimal, routes, cost-ceiling, defensive-parse]

requires:
  - phase: 04-agent-architecture-cost-bounds
    provides: "Migration 0005 added daily_cost_ceiling_usd column (buggy server_default); spend_get + settings routes reading the column"

provides:
  - "Migration 0006: idempotent repair of over-quoted daily_cost_ceiling_usd values + corrected server_default"
  - "spend_get + settings_get + settings_post: defensive Decimal parse → DEFAULT_DAILY_CEILING_USD on malformed/NULL/<=0"
  - "Tests seeding the real corrupted/NULL/empty data shapes for all fixed routes"

affects: [04-HUMAN-UAT, any future migration extending users table, cost-ceiling display paths]

tech-stack:
  added: []
  patterns:
    - "Defensive Decimal parse: try: Decimal(str) except Exception: DEFAULT (mirrors cost_ceiling.py:149-161)"
    - "Alembic idempotent repair UPDATE: WHERE targets only the exact corrupted form; idempotent on clean values"
    - "0006 frozen vocab: carry _FROZEN_EVENT_TYPES_POST from 0005 forward as _FROZEN_EVENT_TYPES (no new types → no PRE/POST split)"

key-files:
  created:
    - "migrations/versions/0006_p4_cost_ceiling_repair.py"
  modified:
    - "migrations/versions/0005_p4_cost_ceiling.py"
    - "src/gekko/dashboard/routes.py"
    - "tests/unit/test_spend_route.py"
    - "tests/unit/test_settings_route.py"
    - "tests/unit/test_p4_alembic_round_trip.py"

key-decisions:
  - "Migration 0006 repairs data + corrects column default; 0005 source also corrected for fresh-install correctness (revision IDs untouched)"
  - "Downgrade reverses only the column default — data repair intentionally not reversed (re-corrupting cleaned rows has no upside)"
  - "Defensive parse at all 4 read-sites (spend_get, settings_get, settings_post x2) matches cost_ceiling.py guard pattern exactly"
  - "Pre-existing test failures (test_handle_edit_size_stub_acks_and_opens_modal, test_doctor_missing_envvar_exits_nonzero) confirmed pre-existing — not caused by this plan"

patterns-established:
  - "Pattern: Import DEFAULT_DAILY_CEILING_USD inside route functions (not module-level) — consistent with spend_get pattern already in place"
  - "Pattern: All ceiling read-sites use identical try/except → DEFAULT block so future changes touch one recognizable shape"

requirements-completed:
  - COST-02
  - COST-03

duration: 35min
completed: 2026-06-24
---

# Phase 4 Plan 07: /spend 500 Gap-Closure Summary

**Migration 0006 repairs the over-quoted daily_cost_ceiling_usd default from 0005 + defensive Decimal parse at 4 routes.py ceiling read-sites closes the /spend HTTP 500 UAT blocker**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-06-24T13:00:00Z
- **Completed:** 2026-06-24T13:35:00Z
- **Tasks:** 3 (Task 1: migration 0006, Task 2: route defensive parse, Task 3: migration test hardening)
- **Files modified:** 6

## Accomplishments

- Created migration 0006 that idempotently repairs the `'5.00'` (6-char, with literal apostrophe chars) corrupted value back to clean `5.00` (4-char) and corrects the column server_default going forward
- Fixed migration 0005 source `server_default` from `"'5.00'"` to `"5.00"` for fresh-install correctness (revision IDs untouched; live DB repaired by 0006)
- Patched all 4 ceiling read-sites in routes.py (spend_get, settings_get, settings_post error branch, settings_post success branch) with the defensive try/except → DEFAULT pattern from cost_ceiling.py:149-161
- Added 7 new tests: 3 seeding the corrupted/NULL/empty ceiling shapes in spend_get, 1 for settings_get corrupted shape, 3 for 0006 revision-wiring + vocab-chain + repair-SQL specificity

## Operator Step Required

**BEFORE re-testing UAT Test 1, run:**
```
alembic upgrade head
```
Expected output: `Running upgrade 0005_p4_cost_ceiling -> 0006_p4_cost_ceiling_repair, ok`

After this, `alembic current` should show `0006_p4_cost_ceiling_repair (head)` and GET /spend should return 200 with the ceiling displayed as $5.00.

The route patch is defense-in-depth — the migration fixes the stored data. Both layers are needed.

## Task Commits

Each task was committed atomically (TDD: RED → GREEN):

1. **Task 1 RED: 0006 migration tests (failing)** - `348104f` (test)
2. **Task 1 GREEN: Migration 0006 + 0005 source fix** - `9da3225` (feat)
3. **Task 2 RED: Route corrupted/NULL ceiling tests (failing)** - `0d147c3` (test)
4. **Task 2 GREEN: routes.py defensive parse at 4 sites** - `9edf4d6` (fix)

Note: Task 3 (migration test hardening) tests were written as part of Task 1 RED commit. No separate commit needed.

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `migrations/versions/0006_p4_cost_ceiling_repair.py` — New migration: op.execute() repair UPDATE + batch_alter_table server_default correction; frozen vocab carry-forward
- `migrations/versions/0005_p4_cost_ceiling.py` — Source-only fix: `server_default="'5.00'"` → `server_default="5.00"` (fresh-install correctness; revision IDs untouched)
- `src/gekko/dashboard/routes.py` — 4 sites patched: spend_get ceiling parse + settings_get ceiling render + settings_post error/success re-render
- `tests/unit/test_spend_route.py` — 3 new tests: corrupted `"'5.00'"`, NULL, empty ceiling → all assert 200
- `tests/unit/test_settings_route.py` — 1 new test: corrupted `"'5.00'"` ceiling in settings_get → 200 + DEFAULT shown
- `tests/unit/test_p4_alembic_round_trip.py` — 3 new tests: 0006 revision wiring, vocab chain, repair SQL specificity gate

## Decisions Made

- Used `server_default="5.00"` (un-quoted) in batch_alter_table so SQLAlchemy renders it as `DEFAULT '5.00'` — the root cause of 0005 was `"'5.00'"` (pre-quoted) which SQLAlchemy rendered as `DEFAULT '''5.00'''`
- Downgrade deliberately does NOT reverse the data repair — re-corrupting cleaned rows has no upside
- Imported `DEFAULT_DAILY_CEILING_USD` inside the route functions (not module-level) to stay consistent with the spend_get pattern already in place
- Each of the 4 settings ceiling reads uses a distinct local alias (`DEFAULT_DAILY_CEILING_USD`, `_DCC_USD`, `_DCC_USD2`) to avoid any scoping collision between the function-local imports

## Deviations from Plan

None — plan executed exactly as written. The 0005 source correction and the 0006 migration both matched the plan's action spec precisely.

## Known Stubs

None — all read-sites now produce a real Decimal value (DEFAULT_DAILY_CEILING_USD = Decimal("5.00")) for any malformed/NULL/empty input.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond what the plan's threat model covered.

## Issues Encountered

Two pre-existing test failures were discovered during the full regression sweep — confirmed pre-existing (failing before this plan's first commit):
- `test_handle_edit_size_stub_acks_and_opens_modal` — edit-size Bolt action retired (D-62); test expects `views_open` but stub logs a retirement warning and acks only
- `test_doctor_missing_envvar_exits_nonzero` — CLI doctor exits 0 when env vars missing

Both are out-of-scope for this plan. Logged here for tracking; deferred to a future plan.

## Next Phase Readiness

- Operator must run `alembic upgrade head` to advance live DB to 0006 (repairs the stored `'5.00'` → `5.00`)
- After migration, UAT Test 1 (/spend 500) should be resolved; Tests 2–4 (80%/100% Slack DMs, hard-halt+reset, suspicious_content) are unblocked

## Self-Check: PASSED

- migrations/versions/0006_p4_cost_ceiling_repair.py: FOUND
- .planning/phases/04-agent-architecture-cost-bounds/04-07-SUMMARY.md: FOUND
- Commit 348104f (test RED): FOUND
- Commit 9da3225 (feat migration 0006): FOUND
- Commit 0d147c3 (test RED routes): FOUND
- Commit 9edf4d6 (fix routes defensive parse): FOUND
- Commit 4e586bf (docs metadata): FOUND

---
*Phase: 04-agent-architecture-cost-bounds*
*Completed: 2026-06-24*
