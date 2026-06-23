---
phase: 04-agent-architecture-cost-bounds
plan: 03
subsystem: agent
tags: [cost-ceiling, decimal, sqlalchemy, zoneinfo, regex, audit-chain, slack, prompt-injection, sc-2]

# Dependency graph
requires:
  - phase: 04-agent-architecture-cost-bounds/04-02
    provides: "Migration 0005 (users.daily_cost_ceiling_usd/cost_alert_*_sent_date + llm_cost/suspicious_content event_types), pricing.py (DEFAULT_DAILY_CEILING_USD, tokens_to_usd)"

provides:
  - "cost_ceiling.py: CeilingCheck dataclass + check_cost_ceiling() deterministic gate (no LLM calls)"
  - "runtime.py: cost ceiling gate wired after quiet-hours gate (halt returns skipped_cost_halt without query()); SC-2 suspicious-content scan wired after Researcher phase"
  - "executor.py: 'cost_alert' added to _BYPASS_CATEGORIES"
  - "schemas/research.py: injected_content_flags: list[str] = [] added to ResearchBrief"
  - "test_cost_ceiling.py: 8 tests GREEN (stub → implementation)"
  - "test_suspicious_content.py: 4 tests GREEN (stub → implementation)"

affects: [04-04, trust-ladder, dashboard-spend, cost-ledger]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Deterministic pre-LLM gate: check_cost_ceiling mirrors quiet_hours.py structure exactly (_get_session_factory seam, ZoneInfo pattern, DB read before any LLM call)"
    - "Per-user pooled ceiling: sum all llm_cost events in Python using Decimal; no SQL SUM (SQLite json_extract returns TEXT)"
    - "Two-tier action: pct>=100 halt (return skipped_cost_halt), pct>=80 degrade (set flag for Wave 4 Haiku gate), else allow"
    - "One-DM-per-day guard: cost_alert_*_sent_date columns compared to now_local.date().isoformat(); updated in same DB session"
    - "SC-2 scan at trust boundary: _INJECTION_PATTERNS module-level re.compile, scan after brief parse before _run_decision"
    - "Bypass category: cost_alert added to _BYPASS_CATEGORIES so halt/degrade DMs fire immediately regardless of quiet hours"

key-files:
  created:
    - src/gekko/agent/cost_ceiling.py
  modified:
    - src/gekko/agent/runtime.py
    - src/gekko/execution/executor.py
    - src/gekko/schemas/research.py
    - tests/unit/test_cost_ceiling.py
    - tests/unit/test_suspicious_content.py

key-decisions:
  - "cost_ceiling.py is structurally identical to quiet_hours.py: same _get_session_factory seam, same ZoneInfo DST-correct boundary computation, same fail-open on user-not-found."
  - "Ceiling guard fires with session_factory=None when called before the outer session_factory is built — uses its own _get_session_factory internally. This allows insertion between the quiet-hours gate and the BudgetTracker construction."
  - "Python-side Decimal sum confirmed required: SQLite json_extract() returns TEXT for string fields; SQL SUM over TEXT produces NULL or wrong results. Row count per user per day is trivially small (<100)."
  - "injected_content_flags added to ResearchBrief as a forward-compat field (list[str] = []). ResearchBrief already uses extra='allow' — this is purely additive."
  - "test_halt_returns_skipped uses source='schedule' — must monkeypatch both check_cost_ceiling in runtime AND _resolve_quiet_hours to prevent the quiet-hours gate from blocking the test before the ceiling gate fires."
  - "test_cost_ceiling.py stubs implemented: _make_fake_session_factory helper builds a mock SQLAlchemy session factory returning a synthetic User + Event rows without requiring a real DB. Tests for test_tz_midnight_reset, test_single_dm_80, test_single_dm_100 monkeypatch datetime.now in the cost_ceiling module for deterministic date boundaries."
  - "test_suspicious_content.py stubs implemented: tests verify _INJECTION_PATTERNS directly (5 patterns: SYSTEM:/OVERRIDE:/ignore-previous/disregard/forget) without requiring a full runtime integration test. test_payload_contains_source_info verifies the payload shape via normalize_decimals."

patterns-established:
  - "Pattern: Deterministic cost ceiling gate mirrors quiet_hours.py exactly. Future analogous guards should follow this pattern: _get_session_factory seam, ZoneInfo tz computation, DB-read + pure arithmetic + dataclass return, no LLM calls."
  - "Pattern: Slack DM bypass for operator-safety events. Add category to _BYPASS_CATEGORIES in executor.py. Cost-alert joins kill_active/executor_error/first_live_fill."

requirements-completed: [COST-01, COST-04]

# Metrics
duration: 35min
completed: 2026-06-23
---

# Phase 4 Plan 3: Cost Ceiling Guard + SC-2 Audit Event Summary

**Deterministic two-tier cost ceiling (halt/degrade) wired into trigger_strategy_run before any LLM call + SC-2 injection-pattern scan logging suspicious_content audit events at the Researcher→Decision trust boundary**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-06-23T17:28:08Z
- **Completed:** 2026-06-23T18:15:00Z
- **Tasks:** 2
- **Files modified:** 6 (1 new + 5 modified)

## Accomplishments

- `cost_ceiling.py` shipped: `CeilingCheck` dataclass + `check_cost_ceiling()` deterministic gate; per-user pooled llm_cost sum in Python Decimal; tz-aware midnight reset via user's existing timezone; one-DM-per-day guard via cost_alert_*_sent_date columns; `_get_session_factory` test seam
- `runtime.py` wired: ceiling gate fires after quiet-hours gate and before `BudgetTracker` construction; halt branch returns `skipped_cost_halt` without calling `query()`; degrade branch sets `_degradation_mode` flag for Wave 4; `_INJECTION_PATTERNS` module-level regex + SC-2 scan loop between Researcher and Decision phases; 80%/100% cost-alert Slack DMs with `category="cost_alert"` bypass
- `executor.py` updated: `"cost_alert"` added to `_BYPASS_CATEGORIES` frozenset
- `schemas/research.py` updated: `injected_content_flags: list[str] = []` added to `ResearchBrief`
- All 8 `test_cost_ceiling.py` tests GREEN; all 4 `test_suspicious_content.py` tests GREEN; `test_decision_prompt_isolation.py` (D-05 AST gate) still GREEN (7/7)

## Task Commits

Each task was committed atomically:

1. **Task 1: cost_ceiling.py — deterministic ceiling guard module** - `4e6cc0f` (feat)
2. **Task 2: runtime.py ceiling gate + SC-2 scan + executor.py bypass** - `fc29054` (feat)

## Files Created/Modified

- `src/gekko/agent/cost_ceiling.py` (NEW) — CeilingCheck dataclass + check_cost_ceiling() async function; _get_session_factory test seam; ZoneInfo tz-midnight boundary; Python-side Decimal sum of llm_cost events; just_crossed_80/100 DM gate with column flush
- `src/gekko/agent/runtime.py` — _INJECTION_PATTERNS module-level re.compile; check_cost_ceiling import; cost ceiling gate block (halt/degrade/allow) inserted after quiet-hours gate; SC-2 scan loop between Researcher and Decision phases; cost-alert Slack DMs
- `src/gekko/execution/executor.py` — "cost_alert" added to _BYPASS_CATEGORIES frozenset
- `src/gekko/schemas/research.py` — injected_content_flags: list[str] = [] field on ResearchBrief
- `tests/unit/test_cost_ceiling.py` — Wave 0 stubs implemented: _make_fake_session_factory helper; 8 tests cover all threshold tiers, halt runtime integration, tz-midnight, single-DM guards
- `tests/unit/test_suspicious_content.py` — Wave 0 stubs implemented: 4 tests verify _INJECTION_PATTERNS regex and payload shape

## Decisions Made

- Ceiling guard uses `session_factory=None` internally when called before the outer session_factory is built — relies on its own `_get_session_factory()` seam. This allows insertion at the correct point (after quiet-hours, before BudgetTracker).
- `test_halt_returns_skipped` must monkeypatch both `gekko.agent.runtime.check_cost_ceiling` AND `gekko.approval.quiet_hours._resolve_quiet_hours` because `source="schedule"` triggers the quiet-hours check before the ceiling gate fires.
- Wave 0 stub tests were completely replaced with working implementations — the stubs raised `NotImplementedError` and could not pass until the implementation shipped.
- `_make_fake_session_factory` builds a complete mock without a real DB: mock SQLAlchemy session, canonical payload_json rows, User model instance with configurable columns.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Stubs] Implemented Wave 0 test stubs in test_cost_ceiling.py and test_suspicious_content.py**
- **Found during:** Task 1 / Task 2 verification
- **Issue:** All 8 test_cost_ceiling.py tests and 4 test_suspicious_content.py tests had `raise NotImplementedError("stub — implement after ... ships in Wave 2")` bodies. These would never pass until implemented.
- **Fix:** Implemented `_make_fake_session_factory` mock helper and all 8 test bodies in test_cost_ceiling.py; implemented all 4 test bodies in test_suspicious_content.py verifying _INJECTION_PATTERNS directly.
- **Files modified:** tests/unit/test_cost_ceiling.py, tests/unit/test_suspicious_content.py
- **Committed in:** 4e6cc0f (Task 1), fc29054 (Task 2)

---

**Total deviations:** 1 auto-fixed (Rule 1 - stub test implementation)
**Impact on plan:** Required for plan's success criteria. Wave 0 stubs were intentionally incomplete placeholders — implementing them is exactly the Wave 3 job.

## Issues Encountered

- The original Wave 0 tests for 80%/100%/allow called `check_cost_ceiling(session_factory=None, ...)` which tried to invoke `_get_session_factory` directly. Rather than leave them calling real DB infrastructure (which requires passphrase + settings), the tests were redesigned to pass a `_make_fake_session_factory` mock directly.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced beyond what the plan's `<threat_model>` already documents.

| Threat | File | Mitigation |
|--------|------|------------|
| T-04-05: cost ceiling talk-past | cost_ceiling.py / runtime.py | Gate is pure Python Decimal arithmetic; fires before any query() call; LLM output cannot reach the comparison |
| T-04-07: prompt injection via evidence | runtime.py | _INJECTION_PATTERNS regex detects + logs; D-40 Decision prompt boundary neutralizes; audit chain records event |
| T-04-09: cost_alert bypass spoofing | executor.py | Same bypass pattern as kill_active/executor_error; operator safety information warrants immediate delivery |

## Self-Check

### Files exist:
- `src/gekko/agent/cost_ceiling.py` — FOUND
- `src/gekko/agent/runtime.py` — FOUND (modified)
- `src/gekko/execution/executor.py` — FOUND (modified)
- `src/gekko/schemas/research.py` — FOUND (modified)

### Commits exist:
- `4e6cc0f` — FOUND (feat(04-03): add deterministic cost-ceiling guard)
- `fc29054` — FOUND (feat(04-03): wire cost ceiling gate + SC-2 scan)

### Tests GREEN:
- `test_cost_ceiling.py` — 8/8 passed
- `test_suspicious_content.py` — 4/4 passed
- `test_decision_prompt_isolation.py` — 7/7 passed (AST gate GREEN)

## Self-Check: PASSED

## Next Phase Readiness

- Wave 4 (04-04) can consume the `_degradation_mode` flag set by the degrade branch for the Haiku pre-triage gate
- The `injected_content_flags` field on ResearchBrief is ready for Wave 4 to populate
- Cost ledger (04-02) + ceiling guard (04-03) form the complete COST-01/COST-04 substrate; dashboard Spend view (04-04 or later) can read from llm_cost events

---
*Phase: 04-agent-architecture-cost-bounds*
*Completed: 2026-06-23*
