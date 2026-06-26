---
phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
plan: 04
subsystem: anomaly-drawdown-reflex
tags: [anomaly, trust-ladder, drawdown, apscheduler, decimal, quiet-hours-bypass, htmx, pytest, tdd]

# Dependency graph
requires:
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 01
    provides: "migration 0007 StrategyMetadata.anomaly_threshold_pct (server_default '0.10') + anomaly_demotion event type in _EVENT_TYPES / ck_event_type; Wave-0 RED stubs (test_anomaly.py, test_scheduler.py)"
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 02
    provides: "trust.demote_strategy_from_auto (AST-gated SOLE writer of trust_level) + load_trust_level; trust.TRUST_AUTO constant"
  - phase: 02-orderguard-real-money-alpaca-live
    provides: "executor on_fill_event + _send_slack_dm_respecting_quiet_hours + _BYPASS_CATEGORIES; OrderGuard get_orders_open/cancel_order passthroughs; promotion.load_strategy_metadata + stamp_first_live_trade set-once template"
provides:
  - "gekko.anomaly.evaluator.evaluate_drawdown — idempotent, surgical, Decimal-exact single-day-drawdown reflex (demote + cancel + DM on breach)"
  - "gekko.anomaly.evaluator.snapshot_start_of_day_value — persists the STABLE drawdown denominator as a discriminated daily_pnl event (OQ#3, no migration)"
  - "gekko.anomaly.evaluator.reject_pending_auto_proposals_for_anomaly — PENDING→REJECTED with anomaly_demotion reason (OQ#5, reuses reject_proposal edge)"
  - "executor._BYPASS_CATEGORIES gains anomaly_demotion (D-T13); on_fill_event post-fill anomaly hook; _build_broker_for_anomaly raw read-only broker"
  - "scheduler.jobs.register_anomaly_evaluator (3.x IntervalTrigger) + register_market_open_snapshot (CronTrigger) + run_anomaly_evaluator_tick / run_market_open_snapshot NYSE-gated handlers"
  - "strategies_list Surface 6b: red role=alert anomaly notice for strategies auto-demoted today"
affects: [auto-execute-branch, daily-digest, dashboard-strategies-list, scheduler-lifespan]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Set-once idempotent reflex: a non-auto strategy is a no-op WITHOUT computing the drawdown (mirrors stamp_first_live_trade short-circuit)"
    - "STABLE start-of-day denominator persisted at market open as a discriminated daily_pnl event (kind=sod_snapshot) — invisible to the close-digest aggregator (reacts only to fill/error/cap_rejection), so no new event type / migration is needed (OQ#3)"
    - "Anomaly threshold (% of SOD value) is an EARLIER rung than max_daily_loss_usd: the reflex revokes autonomy without ever touching a kill switch or halting trading (D-T11/D-T12)"
    - "Two-surface coverage: post-fill hook (realized loss) + NYSE-gated IntervalTrigger tick (unrealized drift) both call the same evaluate_drawdown (SC-4)"
    - "Anomaly DM bypasses quiet hours via _BYPASS_CATEGORIES (D-T13); auto-exec FYI stays routine — inversion is the documented anti-pattern"

key-files:
  created:
    - src/gekko/anomaly/__init__.py
    - src/gekko/anomaly/evaluator.py
  modified:
    - src/gekko/execution/executor.py
    - src/gekko/scheduler/jobs.py
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/strategies_list.html.j2
    - tests/unit/test_anomaly.py
    - tests/unit/test_executor.py
    - tests/unit/test_scheduler.py

key-decisions:
  - "SOD snapshot persisted on the existing daily_pnl event type with a kind='sod_snapshot' discriminator rather than adding a new event type. The plan's files_modified did NOT include a migration or models.py, and migration 0007 (the frozen CHECK-constraint vocabulary) is already shipped; introducing sod_snapshot would require schema surgery out of scope. The daily-P&L aggregator only reacts to fill/error/cap_rejection events, so the discriminated row is invisible to the close digest — fully auditable, zero migration."
  - "PENDING auto-proposal cancellation reuses approval.proposals.reject_proposal directly (PENDING→REJECTED, actor='anomaly-demotion', reason='anomaly_demotion') instead of adding a new proposals.py helper. The plan listed proposals.py in files_modified, but the directive 'reuse the existing edge — OQ#5' is better satisfied by calling the existing transition than by duplicating it. proposals.py is unchanged; the reuse lives in anomaly/evaluator.reject_pending_auto_proposals_for_anomaly."
  - "The post-fill / scheduler anomaly read uses a RAW (un-OrderGuard-wrapped) AlpacaBroker via _build_broker_for_anomaly. The evaluator only READS get_positions/get_orders_open and CANCELS via cancel_order passthroughs — it never places an order — so it does not need the OrderGuard pipeline (which would require synthesizing a full Strategy+HardCaps, and the permissive synth caps violate the Pydantic ≤0.20 position bound)."
  - "evaluate_drawdown writes a dedicated first-class anomaly_demotion audit event (drawdown_pct, threshold_pct, cancelled_count) IN ADDITION to the trust_demoted event the demote helper writes. The in-app Surface-6b notice + the future digest line read this richer event; demote_strategy_from_auto remains the sole trust_level writer (AST gate intact)."

patterns-established:
  - "Anomaly evaluator mirrors promotion.py / _hard_caps.py exactly: _get_session_factory shim + finally dispose, strategy_id=None trust events keyed by strategy_name, normalize_decimals, Decimal-exact, no claude_agent_sdk import"
  - "Scheduler registrars mirror register_daily_pnl_cron / register_expire_stale_sweep: module:fn string callables (restart-safe pickling), coalesce + max_instances=1, NYSE schedule gate applied inside the handler"

requirements-completed: [TRUST-04]

coverage:
  - id: T1
    description: "evaluate_drawdown idempotent (non-auto = no-op, drawdown not computed), surgical (only named strategy), Decimal-exact with zero-denominator guard; on breach demote+cancel+DM+anomaly_demotion event; anomaly trips earlier than max_daily_loss_usd"
    requirement: "TRUST-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_anomaly.py (13 tests incl. at/above/exactly-threshold, below-threshold no-op, already-propose-only no-op, missing-row no-op, default-threshold, threshold-ordering, surgical, decimal-only static guard, zero-denominator + decimal-exact compute, Surface-6b notice render)"
        status: pass
    human_judgment: false
  - id: T2
    description: "anomaly_demotion in executor._BYPASS_CATEGORIES (bypasses quiet hours, D-T13); on_fill_event post-fill hook calls evaluate_drawdown and swallows its exceptions so they never abort the fill; strategies_list renders the role=alert anomaly notice for demoted-today strategies"
    requirement: "TRUST-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_executor.py (anomaly bypass grep gate, anomaly_demotion DM bypasses quiet hours, post-fill hook invoked once, hook exception swallowed + fill still FILLED) + test_quiet_hours_dm_gate.py AST gate (pass)"
        status: pass
    human_judgment: false
  - id: T3
    description: "register_anomaly_evaluator (3.x IntervalTrigger, coalesce, max_instances=1) + register_market_open_snapshot (CronTrigger 09:30 ET, safe knobs); both NYSE-gated handlers; module:fn string refs; no 4.x AsyncScheduler API"
    requirement: "TRUST-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_scheduler.py (8 tests: existence, IntervalTrigger/CronTrigger shape, replace_existing dedupe, module:fn refs, no-4.x-API source gate)"
        status: pass
    human_judgment: false

# Metrics
duration: 18min
completed: 2026-06-26
status: complete
---

# Phase 5 Plan 04: Anomaly Auto-Demotion Reflex Summary

**Ships the runaway-loop early-warning (TRUST-04 / SC-4): a Decimal-exact single-day-drawdown evaluator that, on breaching the per-strategy `anomaly_threshold_pct` (default 10% of a STABLE start-of-day snapshot), demotes the strategy to propose-only via the AST-gated `trust.demote_strategy_from_auto`, cancels its pending auto-orders (open broker orders + PENDING auto-proposals → REJECTED), writes a first-class `anomaly_demotion` audit event, and fires an urgent quiet-hours-bypassing Slack DM. Wired to run both post-fill (realized loss) and on an NYSE-gated APScheduler 3.x IntervalTrigger tick (unrealized drift), with the denominator pinned by a market-open snapshot job. Surgical to one strategy, idempotent, and an earlier rung than the hard `max_daily_loss_usd` cap — autonomy is revoked before trading is halted.**

## Performance
- **Duration:** ~18 min
- **Completed:** 2026-06-26
- **Tasks:** 3 (all TDD: RED → GREEN)
- **Files:** 9 (2 created, 7 modified across 5 task commits + 2 RED-test commits)

## Accomplishments
- **Anomaly evaluator (`anomaly/evaluator.py`, SC-4):** `evaluate_drawdown(*, user_id, strategy_name, broker) -> bool`. Set-once idempotent guard (a non-auto strategy short-circuits before the drawdown is even computed — mirrors `stamp_first_live_trade`). `_compute_single_day_drawdown_pct` reads the STABLE persisted start-of-day snapshot (denominator) and the current value (Σ market_value of the strategy's watchlist positions + today's signed realized P&L), computes Decimal `(sod − current) / sod`, guards `sod <= 0 → Decimal('0')`, and clamps a book-up case to 0. On breach: cancel pending auto-orders, `demote_strategy_from_auto(reason="anomaly", drawdown_pct=...)`, write the `anomaly_demotion` event, fire the urgent bypass DM.
- **Cancellation (T-05-16):** `_cancel_pending_auto_orders` cancels open broker orders (watchlist-attributed, via `get_orders_open` + `cancel_order` passthroughs — no retry/policy added) AND transitions this strategy's PENDING auto-proposals → REJECTED carrying an `anomaly_demotion` reason (OQ#5 — reuses the existing `reject_proposal` edge; no new state). Returns the cancelled count, surfaced in the DM + event.
- **Executor wiring (D-T13):** `anomaly_demotion` added to `_BYPASS_CATEGORIES` (operator-safety tier — bypasses quiet hours, same as kill/cap-rejection/first-live; the auto-exec FYI DM in Plan 05 stays routine, and inverting them is the documented anti-pattern). `on_fill_event` gained a post-fill anomaly hook that evaluates drawdown after the fill DM; eval/broker-build exceptions are swallowed so they never abort the already-committed fill (mirrors the first-live-stamp swallow).
- **Scheduler (Task 3, APScheduler 3.x):** `register_anomaly_evaluator` (IntervalTrigger, 5-min default, coalesce + max_instances=1) and `register_market_open_snapshot` (CronTrigger 09:30 ET, safe knobs). The NYSE-gated handlers `run_anomaly_evaluator_tick` (unrealized drift) and `run_market_open_snapshot` (stable denominator) enumerate the user's auto-within-caps strategies and call the evaluator / snapshot per strategy with per-strategy error isolation. Module:fn string refs keep them restart-safe in the SQLAlchemyJobStore.
- **In-app notice (Surface 6b):** `strategies_list.html.j2` renders a red `.anomaly-notice` (`role="alert" aria-live="assertive"`) above the table for any strategy auto-demoted today (drawdown %, threshold, cancelled count), reactive and self-clearing; `_load_today_anomaly_notices` reads today's `anomaly_demotion` events (latest per strategy).

## Task Commits
1. **Task 1: anomaly evaluator** — `261b665` (test RED) → `43cb192` (feat GREEN)
2. **Task 2: executor bypass + post-fill hook + in-app notice** — `3169340` (feat; tests added inline, RRED+GREEN in one commit since they extend an existing test file)
3. **Task 3: scheduler tick + snapshot** — `15ef888` (test RED) → `74ff6ad` (feat GREEN)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Synthesized OrderGuard HardCaps violated Pydantic bounds for the anomaly broker**
- **Found during:** Task 2 (post-fill hook integration).
- **Issue:** The first cut of `_build_broker_for_anomaly` synthesized a `Strategy` with `max_position_pct=1` to wrap in OrderGuard; `HardCaps` enforces `max_position_pct <= 0.20` (Pydantic `ValidationError`), so the broker build raised, was swallowed, and `evaluate_drawdown` was never reached — the hook silently did nothing.
- **Fix:** The anomaly path only READS positions/orders and CANCELS via passthroughs (never places an order), so it doesn't need OrderGuard. `_build_broker_for_anomaly` now builds a RAW `AlpacaBroker` directly (mirroring the credential resolution in `_build_broker`), no Strategy/HardCaps synthesis.
- **Files modified:** `src/gekko/execution/executor.py`
- **Commit:** `3169340`

### Scope decisions (documented, not deviations)
- **proposals.py left unchanged.** The plan listed it in `files_modified` for a "PENDING→REJECTED helper", but the OQ#5 directive ("reuse the existing edge — no new state") is best honored by calling the existing `reject_proposal` directly from the evaluator (`reject_pending_auto_proposals_for_anomaly`) rather than duplicating the transition. No new state, no new edge, no proposals.py churn.
- **No new event type / migration for the SOD snapshot.** Persisted as a discriminated `daily_pnl` event (`kind="sod_snapshot"`) invisible to the close-digest aggregator — see key-decisions.

## Decisions Made
See `key-decisions` frontmatter: discriminated-daily_pnl SOD snapshot (no migration), reject_proposal reuse for OQ#5, raw read-only broker for the anomaly path, and the dedicated `anomaly_demotion` event alongside `trust_demoted`.

## Known Stubs
None. The evaluator reads live broker positions + the persisted snapshot + the audit log; the executor hook, scheduler jobs, and in-app notice are wired to live data. The scheduler registrars must be invoked by the FastAPI lifespan to be live in production — registration is the deliverable here; lifespan wiring of the per-user jobs follows the existing `register_daily_pnl_cron` / `register_expire_stale_sweep` lifespan pattern (Plan 05-05 / serve path).

## Threat Flags
None beyond the plan's `<threat_model>` (T-05-15..19, all mitigated: anomaly is an earlier rung that stacks with the hard caps + portfolio caps + kill switch; cancellation covers broker orders AND PENDING auto-proposals with the cancelled count in the DM+event; the DM bypasses quiet hours; the denominator is a STABLE persisted snapshot; the reflex is surgical + idempotent).

## Issues Encountered
- `scheduler/jobs.py` carries 2 pre-existing/tolerated ruff findings (UP037 quoted `Engine` annotation on the pre-existing `build_scheduler`; one TRY300 in a new handler matching the file's existing tolerated style). The new `anomaly/` package is ruff-clean; my executor additions are ruff-clean.
- Test env: ran via `.venv/Scripts/python.exe -m pytest` per MEMORY; scoped to the plan's named files (full suite hangs at exit, exit 124 ≠ failure).

## Next Phase Readiness
- Plan 05 (auto-execute branch) inherits the reflex automatically: an auto-executed proposal that fills routes through `on_fill_event`, whose post-fill hook now evaluates drawdown; the scheduler tick covers the no-fill drift case. The auto-exec informational DM Plan 05 adds must use a ROUTINE category (NOT the new `anomaly_demotion` bypass) — the inversion guard is documented.
- The FastAPI serve lifespan should call `register_anomaly_evaluator` + `register_market_open_snapshot` per user alongside the existing `register_daily_pnl_cron` registration so the two new persistent jobs are armed at boot.

## Self-Check: PASSED

---
*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Completed: 2026-06-26*
