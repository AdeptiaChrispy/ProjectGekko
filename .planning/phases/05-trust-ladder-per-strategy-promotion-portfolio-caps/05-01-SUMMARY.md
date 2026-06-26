---
phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, sqlcipher, audit-chain, trust-ladder, portfolio-caps, pytest]

# Dependency graph
requires:
  - phase: 04-agent-architecture-cost-bounds
    provides: "migration head 0006_p4_cost_ceiling_repair + the frozen-vocab migration convention + daily_cost_ceiling_usd money-as-TEXT column shape"
  - phase: 02-orderguard-real-money-alpaca-live
    provides: "StrategyMetadata table, Proposal.account_mode (BLOCKER #5 locked row), approve_proposal/cap_rejection audit writers, OrderGuardRejected"
provides:
  - "Alembic 0007 migration: 4 StrategyMetadata trust/cap/anomaly columns + 4 User portfolio-cap columns + 5 new events CHECK types"
  - "models.py ORM mirror of all 8 new columns + _EVENT_TYPES extension (trust_promoted, trust_demoted, anomaly_demotion, capital_scaled, auto_execution)"
  - "approval audit payloads carry strategy_name + account_mode; cap_rejection payloads carry strategy_name (the streak-scanner data contract)"
  - "9 Wave-0 RED test stubs + conftest seed_approval_events/seed_cap_rejection fixtures for every TRUST-* requirement"
affects: [trust-helpers, streak-scanner, portfolio-caps, capital-ceiling, anomaly-evaluator, auto-execute, trust-dashboard-routes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Frozen-local migration vocabulary: _FROZEN_EVENT_TYPES_PRE == current head's POST; _POST = _PRE + new types"
    - "Un-quoted alembic server_default (0006 repair lesson) vs text(\"'...'\") in the ORM mapped_column"
    - "Enrich audit payload at write time from the LOCKED row so the downstream scanner can attribute without re-deriving (TOCTOU-safe, BLOCKER #5)"

key-files:
  created:
    - migrations/versions/0007_p5_trust_ladder.py
    - tests/unit/test_trust_streak.py
    - tests/unit/test_trust_routes.py
    - tests/unit/test_portfolio_caps.py
    - tests/unit/test_capital_ceiling.py
    - tests/unit/test_anomaly.py
    - tests/unit/test_auto_execute.py
    - tests/unit/test_trust_safety_invariants.py
    - tests/unit/test_migration_0007.py
    - tests/unit/test_scheduler.py
  modified:
    - src/gekko/db/models.py
    - src/gekko/approval/proposals.py
    - src/gekko/execution/executor.py
    - tests/conftest.py

key-decisions:
  - "0007 down_revision pinned to 0006_p4_cost_ceiling_repair (single head), NOT 0005 — pinning to 0005 would skip the 0006 repair and corrupt the chain"
  - "Alembic server_default passed UN-QUOTED ('propose-only', '1000.00', '0.10', '0.50'...) per the 0006 repair lesson; the ORM mapped_column uses text(\"'...'\") which is the correct ORM-side form"
  - "approve_proposal sources strategy_name from the LOCKED Strategy row (keyed by Proposal.strategy_id) and account_mode from the locked Proposal row — never re-derived from live strategy state (BLOCKER #5 TOCTOU-safe)"
  - "test_trust_streak.py + test_anomaly.py use HARD imports of not-yet-built modules (intentional Nyquist RED scaffold per plan); the other 7 stubs use importorskip/skipif and collect now"

patterns-established:
  - "Phase-5 event types are first-class (never event_type='error' + context discriminator — BL-01 anti-pattern)"
  - "Money/percent columns stored as TEXT (percent = fraction string '0.50' == 50%)"

requirements-completed: [TRUST-01, TRUST-02, TRUST-03, TRUST-04, TRUST-05, TRUST-06]

coverage:
  - id: D1
    description: "Alembic 0007 migration chains from 0006 (single head), adds 8 columns + extends ck_event_type with 5 types, round-trips"
    requirement: "TRUST-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_migration_0007.py (revision wiring, frozen-vocab PRE==0006, POST adds 5, ORM column presence)"
        status: pass
      - kind: integration
        ref: "tests/unit/test_migration_0007.py::test_0007_alembic_round_trip (subprocess upgrade/downgrade/upgrade)"
        status: unknown
    human_judgment: false
  - id: D2
    description: "ORM mirror: StrategyMetadata + User columns and _EVENT_TYPES agree with the migration vocabulary"
    requirement: "TRUST-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_migration_0007.py::test_0007_models_event_types_match_frozen_post / test_0007_strategy_metadata_orm_has_trust_columns / test_0007_user_orm_has_portfolio_cap_columns"
        status: pass
    human_judgment: false
  - id: D3
    description: "approval + cap_rejection audit payloads carry strategy_name (+ account_mode for approvals), sourced from the locked rows"
    requirement: "TRUST-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_approval_proposals.py -k 'approve_proposal or strategy_name or account_mode' (14 pass)"
        status: pass
      - kind: unit
        ref: "tests/unit/test_orderguard.py (cap_rejection paths green)"
        status: pass
    human_judgment: false
  - id: D4
    description: "9 Wave-0 RED stubs + conftest seed helpers exist for every TRUST-* requirement (Nyquist scaffold)"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "pytest --collect-only over the 9 files: migration + 6 importorskip-gated collect; streak/anomaly intentionally hard-RED"
        status: pass
    human_judgment: false
  - id: D5
    description: "AST safety gate: no module outside strategy/trust.py assigns trust_level = 'auto-within-caps'"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_trust_safety_invariants.py::test_no_module_outside_trust_assigns_auto_within_caps"
        status: pass
    human_judgment: false

# Metrics
duration: 10min
completed: 2026-06-26
status: complete
---

# Phase 5 Plan 01: Trust-Ladder Schema + Audit-Payload Foundation Summary

**Alembic 0007 adds 4 StrategyMetadata trust/capital/anomaly columns + 4 User portfolio-cap columns + 5 first-class event types, mirrored in the ORM; approval/cap_rejection payloads now carry the strategy+mode attribution the clean-streak scanner depends on; 9 Wave-0 RED stubs scaffold every TRUST-* requirement.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-06-26T15:32:31Z
- **Completed:** 2026-06-26T15:43Z
- **Tasks:** 3
- **Files modified:** 14 (10 created, 4 modified)

## Accomplishments
- Schema floor for all of Phase 5: migration 0007 (single head, chained from 0006) adds `trust_level`/`trust_promoted_at`/`capital_ceiling_usd`/`anomaly_threshold_pct` to `strategy_metadata`, the four account-wide portfolio caps to `users`, and extends `ck_event_type` with `trust_promoted`/`trust_demoted`/`anomaly_demotion`/`capital_scaled`/`auto_execution`.
- ORM mirror in `models.py` kept exactly in sync with the migration vocabulary (verified by equality tests).
- Audit-payload enrichment: `approve_proposal` writes `strategy_name` + `account_mode` (from the locked rows) and both `cap_rejection` sites write `strategy_name` — the data contract the Plan-02 streak scanner reads to partition approvals and zero the streak on a cap breach.
- 9 Wave-0 RED test stubs + two conftest seed fixtures (`seed_approval_events`, `seed_cap_rejection`) scaffold every TRUST-01..06 requirement.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave-0 test stubs + conftest fixtures** - `3ae2307` (test)
2. **Task 2: ORM columns + _EVENT_TYPES + Alembic 0007** - `8ce37d8` (feat)
3. **Task 3: enrich approval + cap_rejection payloads** - `4cb8972` (feat)

## Files Created/Modified
- `migrations/versions/0007_p5_trust_ladder.py` - 8 new columns + 5-type CHECK extension; down_revision=0006; un-quoted server_defaults
- `src/gekko/db/models.py` - 4 StrategyMetadata + 4 User columns + 5 `_EVENT_TYPES`
- `src/gekko/approval/proposals.py` - approval payload enriched with strategy_name + account_mode from locked rows
- `src/gekko/execution/executor.py` - both cap_rejection payloads enriched with strategy_name
- `tests/conftest.py` - seed_approval_events + seed_cap_rejection helpers
- `tests/unit/test_migration_0007.py` - 6 in-process logic tests (round-trip skips on Windows)
- `tests/unit/test_trust_streak.py`, `test_trust_routes.py`, `test_portfolio_caps.py`, `test_capital_ceiling.py`, `test_anomaly.py`, `test_auto_execute.py`, `test_trust_safety_invariants.py`, `test_scheduler.py` - Wave-0 RED stubs

## Decisions Made
- **down_revision = 0006_p4_cost_ceiling_repair** (single alembic head confirmed via `alembic heads`). Pinning to 0005 would skip the 0006 repair and corrupt the chain.
- **Un-quoted alembic server_default** (`"propose-only"`, `"1000.00"`, `"0.10"`, etc.) per the 0006 repair lesson, where an already-quoted default stored an over-quoted string that broke `Decimal(...)` parsing. The ORM `mapped_column` uses `text("'...'")` — the correct ORM-side form — so the two layers differ deliberately.
- **Audit enrichment sourced from the locked rows** (Proposal.account_mode + the Strategy row keyed by Proposal.strategy_id) — TOCTOU-safe per BLOCKER #5, never re-derived from live strategy state.
- **streak + anomaly stubs use hard imports** (intentional Nyquist RED until Plan 02/04 land those modules); the other 7 stubs use `importorskip`/`skipif` and collect now. This matches the plan's explicit "gate import with importorskip ONLY for the migration test; the rest are EXPECTED RED" instruction.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- A pre-existing, out-of-scope unit failure surfaced when the Task-3 verify `-k` filter matched `test_approval_proposals.py::test_handle_edit_size_stub_acks_and_opens_modal` (a retired D-62 edit-size Bolt handler test that still expects a modal). Verified pre-existing by stashing all Task-3 edits and re-running — it fails without my changes. Left untouched per the SCOPE BOUNDARY and logged to `05-...trust-ladder.../deferred-items.md`. All approval/cap_rejection enrichment tests pass.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Schema + audit-payload + Wave-0 scaffold floor is in place. Plan 02 (streak scanner + trust helpers), Plan 03 (portfolio caps + capital ceiling), Plan 04 (anomaly evaluator + scheduler), and Plan 05 (auto-execute branch + dashboard routes) can now consume the new columns and the enriched audit events.
- The hard-RED `test_trust_streak.py` / `test_anomaly.py` will turn green as `gekko.strategy.streak` / `gekko.anomaly.evaluator` land.
- The subprocess alembic round-trip (`test_0007_alembic_round_trip`) is skipped on Windows (SQLCipher cross-process file-lock); it should be exercised on macOS/Linux CI to confirm the live SQLCipher round-trip.

## Self-Check: PASSED

---
*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Completed: 2026-06-26*
