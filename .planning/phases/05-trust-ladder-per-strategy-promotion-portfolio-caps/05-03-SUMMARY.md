---
phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
plan: 03
subsystem: orderguard-portfolio-caps
tags: [orderguard, portfolio-caps, capital-ceiling, trust-ladder, fastapi, htmx, typer, decimal, pytest]

# Dependency graph
requires:
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 01
    provides: "migration 0007 User portfolio-cap columns (max_total_exposure_pct/max_sector_concentration_pct/max_correlated_ticker_pct/max_total_daily_loss_usd) + StrategyMetadata.capital_ceiling_usd (server_default 1000.00); capital_scaled event type; Wave-0 RED stubs"
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 02
    provides: "trust.py (promote/demote/load + session shim); inline capital-scaling route + strategy_capital.html.j2; _strategy_row partial; /modal/close route"
  - phase: 02-orderguard-real-money-alpaca-live
    provides: "OrderGuard.place_order zero-decorator pipeline; _hard_caps.py (_ref_price_for, _resolve_sector, session shim, >25-position canary); OrderGuardRejected(reject_code, msg, extra)"
provides:
  - "gekko.execution.checks.check_portfolio_caps — four account-wide aggregate caps (portfolio_total_exposure / portfolio_sector_concentration / portfolio_correlated_ticker / portfolio_daily_loss); single get_positions() aggregation; blank cap = disabled"
  - "gekko.execution.checks.check_capital_ceiling — per-strategy deployed-capital ceiling (reject_code capital_ceiling); default $1,000; SELL/de-risk unconstrained"
  - "OrderGuard.place_order runs both new checks after check_hard_caps, before check_qty_price_sanity — every order, HITL + auto (D-T08)"
  - "gekko.strategy.trust.set_capital_ceiling — writes capital_scaled (old->new); never touches trust_level or streak (D-T17)"
  - "dashboard: GET /strategies/{name}/capital/review (increase->confirm modal, decrease->apply); refactored POST /capital delegates to set_capital_ceiling; Settings Portfolio Caps fieldset + four-cap validation"
  - "CLI: gekko strategy scale-capital <name> <amount> (confirm-on-increase)"
affects: [auto-execute-branch, anomaly-evaluator, dashboard-settings, dashboard-strategies-list]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Account-wide caps aggregate over a SINGLE get_positions() call — Alpaca nets one position per ticker, never N×M per-strategy broker fan-out (D-T07 / RESEARCH Pitfall 4)"
    - "Percent caps stored as FRACTION TEXT ('0.50'); Settings form converts fraction<->whole-percent at the view boundary"
    - "Capital scaling is a separate rung: set_capital_ceiling writes capital_scaled and is structurally forbidden from touching trust state (D-T17)"
    - "New checks reuse _hard_caps private helpers (_ref_price_for, _resolve_sector) via in-package import rather than duplicating pricing/sector logic"

key-files:
  created:
    - src/gekko/execution/checks/_portfolio_caps.py
    - src/gekko/execution/checks/_capital_ceiling.py
    - src/gekko/dashboard/templates/capital_scale.html.j2
    - src/gekko/dashboard/templates/_capital_increase_modal.html.j2
  modified:
    - src/gekko/execution/checks/__init__.py
    - src/gekko/execution/orderguard.py
    - src/gekko/strategy/trust.py
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/settings.html.j2
    - src/gekko/cli.py
    - tests/unit/test_portfolio_caps.py
    - tests/unit/test_capital_ceiling.py
    - tests/unit/test_orderguard.py
    - tests/unit/test_settings_route.py

key-decisions:
  - "Capital-ceiling 'this strategy's deployed capital' = Σ market_value of net positions whose ticker is in strategy.watchlist + proposed BUY notional. Alpaca nets one position per ticker, so this is the pragmatic per-strategy attribution (no per-strategy broker holdings exist)."
  - "Reconciled 05-02's inline capital-scaling route: extracted the logic into trust.py::set_capital_ceiling, refactored POST /capital to delegate, and added the missing GET /capital/review modal route. The page now renders the plan-named capital_scale.html.j2 (05-02's strategy_capital.html.j2 left in place, no longer routed)."
  - "Capital ceiling never blocks a SELL or an unpriceable order (proposed_notional <= 0 -> early return): the ceiling bounds NEW deployment; de-risking is always allowed even when already over-ceiling (D-T14)."
  - "Settings stores percent caps as FRACTION TEXT to match the OrderGuard read contract ('0.50'); the form shows whole percents via _pct_fraction_to_display / _validate_pct_cap."

patterns-established:
  - "Each new check mirrors _hard_caps.py exactly (session shim, Decimal-exact, OrderGuardRejected with unique reject_code + extra, equity<=0 early return, >25-position canary, no Agent-SDK import)"
  - "The single OrderGuard pipeline is the only path to the broker — aggregate + capital caps stack inside it so the LLM (and a forged manual approval) cannot reason past them (D-T08)"

requirements-completed: [TRUST-02, TRUST-03]

coverage:
  - id: T1
    description: "check_portfolio_caps (four reject_codes, blank=disabled, single get_positions aggregation) + check_capital_ceiling (default $1,000, watchlist attribution, SELL unconstrained) — Decimal-exact, no Agent-SDK import"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_portfolio_caps.py (10 tests) + tests/unit/test_capital_ceiling.py (8 tests)"
        status: pass
    human_judgment: false
  - id: T2
    description: "place_order runs check_portfolio_caps + check_capital_ceiling after check_hard_caps, before qty_price; zero-decorator + no-SDK gates intact; per-strategy-OK order rejects on aggregate breach"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_orderguard.py::test_place_order_rejects_on_aggregate_portfolio_breach + AST/SDK gates"
        status: pass
    human_judgment: false
  - id: T3
    description: "set_capital_ceiling writes capital_scaled and leaves trust/streak untouched; Settings persists four caps (pct->fraction, blank=disabled, range error); capital review modal on increase; CLI scale-capital"
    requirement: "TRUST-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_settings_route.py (5 new tests: fieldset render, fraction save, out-of-range error, blank-disables, set_capital_ceiling trust-untouched)"
        status: pass
      - kind: manual
        ref: "CLI registers scale-capital; capital_scale.html.j2 + _capital_increase_modal.html.j2 render; /capital/review + POST /capital wired"
        status: pass
    human_judgment: false

# Metrics
duration: 15min
completed: 2026-06-26
status: complete
---

# Phase 5 Plan 03: Portfolio Caps + Capital-Ceiling OrderGuard Slice Summary

**Ships the deterministic cap-enforcement slice: two new OrderGuard checks — four account-wide portfolio caps (`_portfolio_caps.py`) aggregating over a single `get_positions()` call, and a per-strategy deployed-capital ceiling (`_capital_ceiling.py`) — stacked inside the one `place_order` pipeline after `check_hard_caps`; plus the Settings Portfolio-Caps fieldset and the capital-scaling rung (page + confirm-on-increase modal + `set_capital_ceiling` helper + CLI), reconciled against 05-02's prior inline capital route. Every order, HITL or auto, inherits these caps; the LLM cannot reason past them.**

## Performance
- **Duration:** ~15 min
- **Started:** 2026-06-26T16:05Z
- **Completed:** 2026-06-26T16:20Z
- **Tasks:** 3
- **Files:** 14 (4 created, 10 modified across 3 task commits)

## Accomplishments
- **Portfolio aggregate caps (`_portfolio_caps.py`, SC-2):** four account-wide caps — `portfolio_total_exposure`, `portfolio_sector_concentration`, `portfolio_correlated_ticker`, `portfolio_daily_loss` — loaded from the `users` row columns (added Plan 01). All percent caps are FRACTION TEXT; blank/NULL = disabled (early return). Aggregation reads ONE `get_positions()` call and caches sector lookups within the invocation (D-T07 / Pitfall 4 — never N×M per-strategy broker calls). Decimal-exact throughout; reuses `_hard_caps._ref_price_for` and `_resolve_sector`.
- **Capital ceiling (`_capital_ceiling.py`, SC-3):** caps the strategy's total deployed capital (Σ market_value of net positions in `strategy.watchlist` + proposed BUY notional) against `StrategyMetadata.capital_ceiling_usd` (default $1,000, D-T16). `reject_code="capital_ceiling"`. A SELL or unpriceable order contributes zero notional and is never blocked (de-risking is always safe, D-T14).
- **OrderGuard wiring (D-T08):** both checks run after `check_hard_caps` and before `check_qty_price_sanity` on EVERY order. `place_order` stays zero-decorator (Knight-Capital AST gate intact); no second order path exists. A behavioral test proves two-strategy-style aggregate breach: an order inside the per-strategy 20% position cap still rejects with `portfolio_total_exposure` at 59.9% aggregate exposure.
- **Capital-scaling rung reconciliation (TRUST-03):** extracted 05-02's inline route logic into `trust.py::set_capital_ceiling` (writes `capital_scaled` old→new; structurally never touches `trust_level`/streak — D-T17), added the missing `GET /strategies/{name}/capital/review` route (increase → typed-name confirm modal; decrease → apply immediately), and shipped `capital_scale.html.j2` + `_capital_increase_modal.html.j2`. `POST /capital` now delegates to the helper.
- **Settings Portfolio-Caps fieldset (Surface 7, T-05-12):** four-cap fieldset below Quiet Hours + Cost Ceiling; `settings_post` validates each cap (percent 0–100 → stored as fraction, USD non-negative, blank = disabled) with field-specific `.login-error` copy (`"Max total exposure must be between 0 and 100."`).
- **CLI parity (D-T14):** `gekko strategy scale-capital <name> <amount>` with typed-name confirm on increase, delegating to the same `set_capital_ceiling` helper.

## Task Commits
1. **Task 1: two OrderGuard checks** — `dccd913` (feat) — `_portfolio_caps.py` + `_capital_ceiling.py` + barrel + RED→GREEN tests
2. **Task 2: wire checks into place_order** — `492c602` (feat) — orderguard insertion + aggregate-breach behavioral test
3. **Task 3: Settings fieldset + capital rung + CLI** — `0e64a76` (feat) — trust.py helper, routes refactor + review route, templates, settings validation, CLI

## Cross-Plan Reconciliation (05-02 → 05-03)
05-02 had deviated and implemented capital scaling inline in `routes.py` (`set_capital_ceiling_route` + `strategy_capital.html.j2`), writing the `capital_scaled` event directly rather than via a helper. Per the prompt's reconciliation directive I did NOT duplicate it:
- **Extracted** the inline logic into `trust.py::set_capital_ceiling` (the plan's named artifact) and refactored `POST /capital` to delegate to it.
- **Filled the gaps** the plan required but 05-02 had not built: the `GET /capital/review` modal route, the `capital_scale.html.j2` + `_capital_increase_modal.html.j2` templates (05-02's page was named `strategy_capital.html.j2` and used a hidden auto-filled confirm field that defeated the typed-confirm intent — the new flow routes increases through a real typed-name modal), the Settings four-cap fieldset, and the CLI `scale-capital`.
- **Left in place** 05-02's `strategy_capital.html.j2` (no longer routed) to avoid touching a committed artifact unnecessarily.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Existing OrderGuard happy-path test needed the new checks' session factories patched**
- **Found during:** Task 2.
- **Issue:** `test_orderguard_place_order_delegates_when_all_checks_pass` now traverses `check_capital_ceiling` (always reads StrategyMetadata) and `check_portfolio_caps` (reads the users row), neither of which had its `_get_session_factory` monkeypatched → `RuntimeError: Passphrase not set`.
- **Fix:** Extended the test's monkeypatch loop to cover `_portfolio_caps` and `_capital_ceiling` (same pattern already used for `_hard_caps`/`_kill_switch`).
- **Files modified:** `tests/unit/test_orderguard.py`
- **Commit:** `492c602`

**2. [Rule 1 - Bug] Misplaced `@router.get("/settings")` decorator caused a 422 regression**
- **Found during:** Task 3 (first run of `test_settings_route.py`).
- **Issue:** Inserting the new cap-display helper functions immediately after the `@router.get("/settings")` decorator attached the decorator to `_pct_fraction_to_display` instead of `settings_get`, so FastAPI registered a route whose handler had a `raw` parameter → every GET /settings returned 422 (`Field required: query.raw`).
- **Fix:** Moved the decorator back onto `settings_get`; helpers are now plain module functions above it.
- **Files modified:** `src/gekko/dashboard/routes.py`
- **Commit:** `0e64a76`

## Decisions Made
- **Watchlist-based capital attribution** — since Alpaca nets one position per ticker (no per-strategy broker holdings), `check_capital_ceiling` sums net positions whose ticker is in `strategy.watchlist`. Documented as the pragmatic per-strategy measure.
- **Percent-as-fraction storage** — Settings stores percents as fraction TEXT (`"0.50"`) to match the OrderGuard cap-read contract; the form converts at the view boundary.
- **SELL/unpriceable orders never trip the ceiling** — the ceiling bounds new deployment; an order adding zero notional returns early so de-risking is always allowed even when already over-ceiling.

## Known Stubs
None. Both checks read live broker positions + DB caps; the Settings fieldset and capital rung are wired to the `User` / `StrategyMetadata` rows. The auto-execute branch that will *also* traverse these caps lands in Plan 05 (it routes through the same `execute_proposal` → OrderGuard last line, so no new enforcement path is needed).

## Threat Flags
None — no new network endpoints, auth paths, or trust-boundary schema beyond the plan's `<threat_model>` (T-05-10..14, all mitigated: caps inside the single place_order pipeline; server-side cap validation; typed-confirm + require_session + user_id filter on capital increase; single get_positions aggregation).

## Issues Encountered
- `routes.py` / `cli.py` carry 22 pre-existing ruff style findings (I001/E501/E402/TRY300 — same count on HEAD before this plan). My additions introduced zero net new findings; `trust.py` and both new check modules are ruff-clean.
- Test env: ran via `.venv/Scripts/python.exe -m pytest` per MEMORY; scoped to the plan's named files (full suite hangs at exit, exit 124 ≠ failure).

## Next Phase Readiness
- Plan 04 (anomaly evaluator) and Plan 05 (auto-execute branch) both inherit these caps automatically — the auto path reaches the broker only through `execute_proposal` → `OrderGuard.place_order`, which now stacks portfolio + capital caps on the per-strategy hard caps.

## Self-Check: PASSED
- Created files verified present: `_portfolio_caps.py`, `_capital_ceiling.py`, `capital_scale.html.j2`, `_capital_increase_modal.html.j2`.
- Commits verified in git log: `dccd913`, `492c602`, `0e64a76`.
- Plan verification suite green: `test_portfolio_caps.py test_capital_ceiling.py test_orderguard.py test_settings_route.py` (58 passed).

---
*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Completed: 2026-06-26*
