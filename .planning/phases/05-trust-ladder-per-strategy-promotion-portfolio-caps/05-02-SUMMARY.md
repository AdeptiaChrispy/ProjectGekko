---
phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
plan: 02
subsystem: trust-ladder
tags: [trust-ladder, autonomy, streak-scanner, htmx, fastapi, typer, ast-gate, pytest]

# Dependency graph
requires:
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 01
    provides: "migration 0007 trust/capital columns + 5 event types; enriched approval/cap_rejection payloads (strategy_name + account_mode); Wave-0 RED stubs + seed fixtures"
  - phase: 02-orderguard-real-money-alpaca-live
    provides: "promotion.py template (session-factory shim, append_event, dispose); promote_to_live route + typed-confirm pattern; CLI promote-live/demote-live shape"
provides:
  - "gekko.strategy.streak.compute_clean_streak — deterministic backward-scan eligibility authority (StreakResult: clean_count, threshold=10, eligible, block_reason, last_breach_date, last_reset_date)"
  - "gekko.strategy.trust — promote_strategy_to_auto / demote_strategy_from_auto / load_trust_level; SOLE writer of trust_level='auto-within-caps'"
  - "dashboard routes: promote-auto/confirm-modal, promote-auto/blocked-modal, POST promote-auto (server re-checks), POST demote-auto, capital scaling page + POST"
  - "AUTO/PROPOSE-ONLY trust badges + capital chip + promote/demote/blocked controls on strategies list"
  - "CLI parity: strategy promote-auto / demote-auto / trust-status (no Slack promote command)"
  - "material-edit reset hook in strategy_save (watchlist/hard_caps change demotes auto strategy, resets streak)"
affects: [auto-execute-branch, anomaly-evaluator, portfolio-caps, dashboard-strategies-list, daily-digest]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Eligibility from the append-only audit log (backward scan, id DESC), never a counter column (D-14)"
    - "Server is the authority: promote routes + CLI re-check compute_clean_streak before promoting (D-T18b); UI is affordance only"
    - "AST gate locks trust_level='auto-within-caps' to a single sanctioned writer (mirrors Phase-4 orderguard zero-decorator gate)"
    - "Material edit modelled as a trust_demoted boundary so the streak scanner needs no snapshot-diffing"

key-files:
  created:
    - src/gekko/strategy/streak.py
    - src/gekko/strategy/trust.py
    - src/gekko/dashboard/templates/_strategy_row.html.j2
    - src/gekko/dashboard/templates/_promote_auto_confirm_modal.html.j2
    - src/gekko/dashboard/templates/_promote_auto_blocked_modal.html.j2
    - src/gekko/dashboard/templates/strategy_capital.html.j2
  modified:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/strategies_list.html.j2
    - src/gekko/dashboard/static/tailwind.css
    - src/gekko/cli.py
    - tests/conftest.py

key-decisions:
  - "compute_clean_streak accepts an AsyncSession directly (matching the Plan-01 RED-stub contract test sig), NOT the promotion.py session-factory shim — the caller owns the session/transaction; streak is read-only"
  - "A cap_rejection closes the clean-count window (break on first id-DESC hit), so clean_count = clean approvals SINCE the most-recent breach (test_cap_rejection_zeroes_the_streak expects 2, not 10)"
  - "Capital scaling route (Surface 3) was implemented here, not deferred to Plan 03 — the Plan-01 RED route stub (test_capital_increase_requires_typed_confirm) demands set_capital_ceiling_route/strategy_capital; the capital_ceiling_usd column already exists from 0007 (Rule 2: missing critical functionality the test contract requires)"
  - "conftest seed fixtures gained an _ensure_user guard — events.user_id has a FOREIGN KEY to users.user_id that the SQLCipher test engine enforces; the Plan-01 fixtures seeded an orphan user_id and would IntegrityError (Rule 1 fix)"
  - "strategies_list refactored to an included _strategy_row.html.j2 partial so promote/demote POSTs can re-render the row standalone (OOB swap)"

patterns-established:
  - "trust.py mirrors promotion.py verbatim (shim + finally dispose + strategy_id=None + normalize_decimals + no claude_agent_sdk import)"
  - "Blocked-promotion is a first-class rendered outcome (SC-5) — the disabled button routes to an explanation modal; a forged ineligible POST returns the same block, never a silent no-op"

requirements-completed: [TRUST-01, TRUST-05, TRUST-06]

coverage:
  - id: T1
    description: "compute_clean_streak partitions by (strategy_name, account_mode), zeroes on cap_rejection, resets at demotion boundary; StreakResult has the 6 UI-SPEC fields"
    requirement: "TRUST-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_trust_streak.py (4 tests: 10-clean eligible, cap_rejection zeroes, cross-strategy no-bleed, paper/live separate)"
        status: pass
    human_judgment: false
  - id: T2
    description: "trust.py is the sole writer of auto-within-caps; material edit demotes auto strategies; AST gate locks the invariant"
    requirement: "TRUST-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_trust_safety_invariants.py::test_no_module_outside_trust_assigns_auto_within_caps (pass); test_auto_branch_is_guarded_by_trust_check (skipped — runtime branch lands Plan 05)"
        status: pass
    human_judgment: false
  - id: T3
    description: "promote re-checks eligibility server-side (forged/ineligible POST -> blocked block, never promotes); demote one-click; capital typed-confirm on increase; CLI parity; no Slack promote"
    requirement: "TRUST-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_trust_routes.py (3 tests: promote_to_auto / demote_from_auto / capital route symbols + contract)"
        status: pass
      - kind: manual
        ref: "grep: no promote/demote in src/gekko/slack/; templates render; CLI registers promote-auto/demote-auto/trust-status"
        status: pass
    human_judgment: false

# Metrics
duration: 12min
completed: 2026-06-26
status: complete
---

# Phase 5 Plan 02: Trust State + Promote/Demote Surface Summary

**Ships the autonomy axis: a deterministic clean-streak scanner over the append-only audit log, a `trust.py` helper that is the sole writer of `auto-within-caps` (AST-locked), dashboard promote/demote/blocked surfaces with server-side eligibility re-checks, CLI parity, and the material-edit streak-reset hook — with blocked-with-explanation as a first-class SC-5 outcome.**

## Performance
- **Duration:** ~12 min
- **Started:** 2026-06-26T15:46Z
- **Completed:** 2026-06-26T15:58Z
- **Tasks:** 3
- **Files:** 16 (6 created, 5 modified across 3 task commits)

## Accomplishments
- **Eligibility authority (`streak.py`):** `compute_clean_streak` walks the events log `id DESC`, partitions `approval` events by `(strategy_name, account_mode)`, zeroes the clean count at the most-recent `cap_rejection`, and stops at the most-recent `trust_demoted`/`anomaly_demotion` boundary (with `material_edit` → `block_reason="material_edit_reset"`). Returns the exact six-field `StreakResult` UI-SPEC Surface 5 consumes. No counter column — the audit log is the source of truth (D-14).
- **Trust helpers (`trust.py`):** `promote_strategy_to_auto` / `demote_strategy_from_auto` / `load_trust_level`, mirroring `promotion.py` exactly (session-factory shim, `finally: dispose`, `strategy_id=None`, `normalize_decimals`, no `claude_agent_sdk` import). This module is the **sole** assigner of `trust_level="auto-within-caps"` — locked by the AST gate.
- **Material-edit reset (D-T05):** `strategy_save` now compares the new snapshot's `watchlist`/`hard_caps` against the prior version; a material change on an `auto-within-caps` strategy emits `trust_demoted reason=material_edit`, resetting the streak window. Thesis/schedule-only edits do not trigger.
- **Dashboard surfaces (SC-1/SC-5):** AUTO ✓ / PROPOSE-ONLY badges + capital chip on the strategies list; promote-confirm modal (typed-name), blocked-explanation modal (streak meter + per-reason copy), one-click demote with next-cycle status. The `POST /promote-auto` route re-checks `compute_clean_streak` server-side and returns the blocked block for any forged/ineligible request — never a silent promote (D-T18b).
- **Capital scaling (Surface 3):** dedicated page + `POST /capital` writing `capital_scaled` events; lowering applies immediately, raising requires a typed-name confirm — trust level + streak untouched (D-T17).
- **CLI parity (D-T04):** `gekko strategy promote-auto` (typed-confirm + server eligibility re-check), `demote-auto`, `trust-status`. No Slack promote/demote command anywhere.

## Task Commits
1. **Task 1: streak scanner** — `8c15179` (feat) — streak.py + conftest `_ensure_user` FK fix
2. **Task 2: trust.py + material-edit hook + AST gate** — `fa3f61b` (feat)
3. **Task 3: routes + modals + badges + CLI** — `0bcb89b` (feat)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] conftest seed fixtures violated the events->users FK**
- **Found during:** Task 1 (first run of `test_trust_streak.py`).
- **Issue:** The Plan-01 Wave-0 fixtures `seed_approval_events` / `seed_cap_rejection` write events for `user_id="u1"` without a parent `users` row. `events.user_id` carries a `FOREIGN KEY` to `users.user_id`, which the SQLCipher test engine enforces → `IntegrityError: FOREIGN KEY constraint failed`. The streak test could not run as written.
- **Fix:** Added an idempotent `_ensure_user(session, user_id)` helper in conftest, called at the top of both seed callables, inserting the minimal `User(user_id, created_at)` row once.
- **Files modified:** `tests/conftest.py`
- **Commit:** `8c15179`

**2. [Rule 2 - Missing critical functionality] Capital-scaling route implemented here**
- **Found during:** Task 3 (route test collection).
- **Issue:** The Plan-01 RED route stub `tests/unit/test_trust_routes.py::test_capital_increase_requires_typed_confirm` asserts the dashboard module exposes `set_capital_ceiling_route` or `strategy_capital`. Plan 02's named scope is promote/demote/blocked, but the verify command (`pytest test_trust_routes.py -x`) cannot pass without this route existing. The `capital_ceiling_usd` column already shipped in migration 0007.
- **Fix:** Implemented Surface 3 (capital page GET + `POST /capital` with typed-confirm on increase, `capital_scaled` audit event, trust/streak untouched) per UI-SPEC, plus the `strategy_capital.html.j2` page.
- **Files modified:** `src/gekko/dashboard/routes.py`, `src/gekko/dashboard/templates/strategy_capital.html.j2`
- **Commit:** `0bcb89b`

## Decisions Made
- **`compute_clean_streak(session=...)`** takes an `AsyncSession` directly (matching the RED-stub contract test signature), not the `promotion.py` factory shim. The scanner is read-only; the caller owns the session/transaction.
- **cap_rejection closes the clean-count window** (`break` on the first id-DESC hit): `clean_count` is the run of clean approvals *since the most-recent breach* — the `test_cap_rejection_zeroes_the_streak` contract (8 → breach → 2) expects `clean_count == 2`.
- **strategies_list refactored to a `_strategy_row.html.j2` partial** so promote/demote POSTs can re-render a single row standalone (OOB swap flips the badge in place).

## Known Stubs
None. The trust badge, streak meter, and promote/demote/blocked surfaces are wired to live data (`compute_clean_streak` + `StrategyMetadata`). The runtime auto-execute branch that *consumes* `load_trust_level` is intentionally deferred to Plan 05 — the AST gate's Invariant-2 test is correctly `skipif`-gated until that branch lands.

## Issues Encountered
- `routes.py` / `cli.py` carry pre-existing ruff style findings (TRY003/TRY300 etc., 20 pre-existing on HEAD). My additions introduced only the same style-rule classes the file already tolerates; the new modules `streak.py` and `trust.py` are ruff-clean. No functional issues.

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
- Plan 04 (anomaly evaluator) can call `demote_strategy_from_auto(reason="anomaly", drawdown_pct=...)` and read `load_trust_level`.
- Plan 05 (auto-execute branch in `runtime.py`) can call `load_trust_level` to gate the auto path; when it lands, `test_auto_branch_is_guarded_by_trust_check` un-skips and enforces Invariant 2.
- Plan 03 (portfolio caps) inherits the `_strategy_row` partial + the capital ceiling that OrderGuard's `_capital_ceiling.py` check will read.

## Self-Check: PASSED

---
*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Completed: 2026-06-26*
