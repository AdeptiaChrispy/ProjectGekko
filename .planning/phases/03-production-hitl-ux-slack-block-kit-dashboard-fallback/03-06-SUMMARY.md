---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 06
subsystem: reporter
tags: [daily-pnl, rept-01, slack-block-kit, apscheduler, severity-tier, ast-gate, tdd]
dependency_graph:
  requires:
    - 03-01  # schema substrate (daily_pnl event_type in _EVENT_TYPES)
    - 03-03  # _send_slack_dm_respecting_quiet_hours + bypass/routine categories
    - 03-04  # register_expire_stale_sweep pattern for APScheduler registrar
  provides:
    - send_daily_pnl_digest async cron entry point (D-59 NYSE gate + D-48 routine category)
    - register_daily_pnl_cron APScheduler registrar (CronTrigger 16:30 ET)
    - _build_digest_blocks Block Kit builder per UI-SPEC §Surface 6
    - severity-tier emoji prefix on MarketClosed (⚠️) + BrokerOrderError (❌)
    - kill_switch 🚫 prefix verified present (already existed)
    - AST gate: every FAILED transition in executor.py has sibling _send_slack_dm call
  affects:
    - src/gekko/reporter/daily_pnl.py
    - src/gekko/scheduler/jobs.py
    - src/gekko/dashboard/app.py
    - src/gekko/execution/executor.py
    - tests/unit/test_daily_pnl_aggregation.py
    - tests/unit/test_daily_pnl_respects_quiet.py
    - tests/unit/test_severity_tier_dm.py
    - tests/unit/test_executor_error_dms_coverage.py
    - tests/integration/test_sweep_persistence.py
tech_stack:
  added: []
  patterns:
    - DigestData dataclass for audit-log aggregation results
    - _aggregate_today_events: ET date window converted to UTC for WHERE clause
    - _build_digest_blocks: Block Kit header/section/section/context/actions shape
    - _send_dm_blocks_respecting_quiet_hours: ROUTINE category routes through quiet-hours gate
    - register_daily_pnl_cron: same APScheduler module:fn string ref pattern as sweep registrar
    - AST walk over source files to assert no silent-FAILED transitions
key_files:
  created:
    - src/gekko/reporter/daily_pnl.py
    - tests/unit/test_daily_pnl_aggregation.py
    - tests/unit/test_daily_pnl_respects_quiet.py
    - tests/unit/test_severity_tier_dm.py
    - tests/unit/test_executor_error_dms_coverage.py
  modified:
    - src/gekko/scheduler/jobs.py
    - src/gekko/dashboard/app.py
    - src/gekko/execution/executor.py
    - tests/integration/test_sweep_persistence.py
decisions:
  - "daily_pnl is ROUTINE category (D-48) — goes through _send_slack_dm_respecting_quiet_hours, defers in quiet window"
  - "_send_dm_blocks_respecting_quiet_hours in daily_pnl.py is a local helper (not imported from executor.py) to avoid circular import; pattern mirrors executor.py's _send_slack_dm_respecting_quiet_hours"
  - "cap_rejection paths already emit Block Kit cards via build_orderguard_rejection_card — no plain text ❌ DM needed"
  - "kill_switch 🚫 prefix already present in _dm_kill_summary — no code change required for Task 3"
  - "AST gate uses 30-line window for sibling DM detection (larger than the 5-line AST gate in test_quiet_hours_dm_gate.py) to accommodate the try/except + DM pattern in executor.py"
  - "DigestData gross_pnl_usd uses float() for format string (${value:+,.2f}) since Decimal format spec with sign doesn't work with Python's format builtin directly"
metrics:
  duration_minutes: 45
  completed: "2026-06-18"
  tasks_completed: 3
  files_created: 5
  files_modified: 4
---

# Phase 3 Plan 6: Daily P&L Digest (REPT-01) + Severity-Tier Emoji Prefixes Summary

**One-liner:** 16:30 ET daily P&L Block Kit DM via APScheduler cron with D-59 NYSE schedule gate, D-48 routine quiet-hours semantics, severity-tier emoji prefixes on all executor + kill DMs (⚠️/❌/🚫), and an AST gate ensuring zero silent-FAILED transitions.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 — send_daily_pnl_digest + D-59 gate + Block Kit builder | b3654fa | NEW daily_pnl.py: NYSE schedule gate, audit aggregation, Block Kit digest, ROUTINE DM dispatch; 7 unit tests |
| 2 — register_daily_pnl_cron + lifespan | 7d355a2 | scheduler/jobs.py: register_daily_pnl_cron (CronTrigger 16:30 ET); dashboard/app.py lifespan: registers after expire-stale sweep; integration tests extended |
| 3 — severity-tier emoji + carry-forward AST gate | eeb8c9f | executor.py: ⚠️ on MarketClosed, ❌ on BrokerOrderError; AST walk gate for FAILED transition coverage; 5 unit tests |

## What Was Built

### `src/gekko/reporter/daily_pnl.py` (NEW)

- `send_daily_pnl_digest(*, user_id: str) -> bool` — cron entry point. Returns True if DM dispatched, False if skipped (D-59 market-closed day).
- `DigestData` dataclass — aggregated fills_count, gross_pnl_usd, per_strategy dict, errors_count, cap_rejections_count, open_positions_count.
- `_aggregate_today_events(session, user_id, today_et)` — SELECT Event rows within today's ET date window (converted to UTC). BUY fills subtract, SELL fills add to gross_pnl_usd. Groups by `strategy_name` in fill payload.
- `_build_digest_blocks(data, today_iso)` — Block Kit list per UI-SPEC §Surface 6: header (📊), gross P&L section (📈/📉), per-strategy section (or `_no fills today_`), context counts block, actions footer with dashboard URL.
- `_send_dm_blocks_respecting_quiet_hours(user_id, *, blocks, category, fallback)` — module-local quiet-hours-aware blocks DM helper. ROUTINE categories check `_resolve_quiet_hours`; bypass categories fire directly.
- Post-DM `daily_pnl` audit event written to chain with `{date, gross_pnl, fills_count, errors_count}` payload (D-45 / T-03-06-04).
- D-59 NYSE schedule gate: `mcal.get_calendar("NYSE").schedule(start_date=today_et, end_date=today_et).empty` → early return False + structlog `daily_pnl.market_closed_skip`.
- Zero `claude_agent_sdk` or `anthropic` imports (deterministic Python firewall).

### `src/gekko/scheduler/jobs.py` (EXTENDED)

- `register_daily_pnl_cron(scheduler, *, user_id) -> str` — new registrar with `CronTrigger(hour=16, minute=30, timezone=ZoneInfo("America/New_York"))`, `coalesce=True`, `max_instances=1`, module:fn string ref.
- Added to `__all__`.

### `src/gekko/dashboard/app.py` (EXTENDED)

- Lifespan imports `register_daily_pnl_cron` alongside `register_expire_stale_sweep`.
- Registers `register_daily_pnl_cron(app.state.scheduler, user_id=user_id)` AFTER `register_expire_stale_sweep` — same scheduler instance (PATTERNS §2f).

### `src/gekko/execution/executor.py` (EXTENDED)

- MarketClosed DM: prepended `⚠️ ` (informational tier per PATTERNS §2l).
- BrokerOrderError DM: prepended `❌ ` (error tier per PATTERNS §2l).
- cap_rejection paths already emit Block Kit rejection cards via `build_orderguard_rejection_card` — no additional plain text DM needed.

### `src/gekko/execution/kill_switch.py` (UNCHANGED)

- `_dm_kill_summary` already contains `🚫 Kill ACTIVE...` (kill-state-change tier). No code change needed.

## Test Coverage

| Test File | Cases | What's Tested |
|-----------|-------|---------------|
| test_daily_pnl_aggregation.py | 4 | Aggregation (3 fills + 1 error), zero fills branch, glyph sign, D-59 gate |
| test_daily_pnl_respects_quiet.py | 3 | DM deferred in quiet window, fires outside, D-59 gate before quiet check |
| test_severity_tier_dm.py | 3 | ⚠️ on MarketClosed, ❌ on BrokerOrderError, 🚫 in kill_switch bytes |
| test_executor_error_dms_coverage.py | 2 | AST walk: every FAILED transition has sibling _send_slack_dm in executor.py + kill_switch.py |
| test_sweep_persistence.py | +2 | daily_pnl cron job registered, persists across restart |

**Total new tests: 14 (7 aggregation/quiet + 5 severity + 2 sweep extension)**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] AlternativeConsidered schema field mismatch in test fixture**
- **Found during:** Task 3 test RED phase
- **Issue:** Test used `ticker`, `description`, `reason_not_chosen` fields but AlternativeConsidered schema has only `description` and `why_rejected` (no ticker field, forbids extras)
- **Fix:** Updated test fixture to use `description` and `why_rejected`
- **Files modified:** `tests/unit/test_severity_tier_dm.py`
- **Commit:** eeb8c9f (same task)

**2. [Rule 1 - Bug] TradeProposal field names in test fixture**
- **Found during:** Task 3 test RED phase
- **Issue:** `alternatives` should be `alternatives_considered`; `wash_sale_flag=False` should be omitted (it's `dict | None`, not bool)
- **Fix:** Updated field names in test fixture
- **Files modified:** `tests/unit/test_severity_tier_dm.py`
- **Commit:** eeb8c9f (same task)

**3. [Rule 1 - Bug] Strategy schema requires `version` field**
- **Found during:** Task 1 test RED phase
- **Issue:** Test helper was building Strategy without `version=1`
- **Fix:** Added `version=1` to both StrategySchema instances in test helper
- **Files modified:** `tests/unit/test_daily_pnl_aggregation.py`
- **Commit:** b3654fa (same task)

**4. [Design clarification] cap_rejection paths already have DMs**
- **Found during:** Task 3 analysis
- **Issue:** Plan says "if no DM currently fires (carry-forward gap), ADD DM with ❌". Both cap_rejection branches already emit Block Kit rejection cards via `_send_slack_dm_blocks(build_orderguard_rejection_card(...))`.
- **Decision:** No new plain text DM needed. The Block Kit card is richer than `❌ OrderGuard rejected {tp.ticker}...`. AST gate covers both transitions.
- **Impact:** Zero deviation from spec intent — operator visibility is satisfied by the existing rejection cards.

**5. [Rule 3 - Blocker] `_send_dm_blocks_respecting_quiet_hours` is module-local in daily_pnl.py**
- **Found during:** Task 1 implementation
- **Issue:** The plan says to add `_send_slack_dm_blocks(user_id, blocks, *, category)` to `executor.py` and have it consult `_resolve_quiet_hours`. However, `_send_slack_dm_blocks` already exists in executor.py (added in Plan 02-05 Task 3) as a blocks-only sender without quiet-hours logic. Adding category-aware routing to the existing function would be a breaking change.
- **Fix:** Created `_send_dm_blocks_respecting_quiet_hours` as a module-local helper in `daily_pnl.py` (same quiet-hours pattern as executor.py's `_send_slack_dm_respecting_quiet_hours`). This preserves PATTERNS §2e (all DMs route through executor's identity-split functions at the final send layer) while avoiding a breaking change to the existing `_send_slack_dm_blocks`.
- **Impact:** Cleaner layering; executor.py unchanged.

## Known Stubs

None — all test stubs from Wave 0 were fully populated.

## Threat Surface Scan

No new network endpoints introduced. `daily_pnl.py` sends via the existing Slack client seam. The APScheduler cron job is within the existing scheduler trust boundary. The NYSE calendar data is bundled locally (T-03-06-01 — no network call at gate-check time).

**T-03-06-04 mitigation confirmed:** `daily_pnl` audit event written to chain after each successful DM, confirming the digest fired. `walk_chain` verifies integrity.

**T-03-06-05 mitigation confirmed:** `_send_dm_blocks_respecting_quiet_hours` uses `settings.slack_user_id` from environment (identity-split seam) — no user-controllable channel injection possible.

**T-03-06-06 mitigation confirmed:** `daily_pnl` category is ROUTINE (not bypass), so it defers during quiet hours. The AST gate `test_quiet_hours_dm_gate.py` still classifies every `_send_slack_dm` call site.

**T-03-06-07 mitigation confirmed:** `test_severity_tier_dm.py` is the regression gate for emoji prefixes.

## Self-Check

Files created/modified verified:
- FOUND: src/gekko/reporter/daily_pnl.py (contains `async def send_daily_pnl_digest`)
- FOUND: src/gekko/scheduler/jobs.py (contains `def register_daily_pnl_cron`)
- FOUND: src/gekko/dashboard/app.py (contains `register_daily_pnl_cron` in lifespan)
- FOUND: src/gekko/execution/executor.py (contains ⚠️ MarketClosed and ❌ BrokerOrderError prefixes)
- FOUND: tests/unit/test_daily_pnl_aggregation.py (4 tests)
- FOUND: tests/unit/test_daily_pnl_respects_quiet.py (3 tests)
- FOUND: tests/unit/test_severity_tier_dm.py (3 tests)
- FOUND: tests/unit/test_executor_error_dms_coverage.py (2 AST tests)
- FOUND: tests/integration/test_sweep_persistence.py (extended with 2 daily_pnl tests)

Commits verified:
- b3654fa — Task 1
- 7d355a2 — Task 2
- eeb8c9f — Task 3

Test run: 22/22 pass (plan tests) + 36/36 Phase 1/2 core executor/kill/quiet-hours chains pass.

## Self-Check: PASSED
