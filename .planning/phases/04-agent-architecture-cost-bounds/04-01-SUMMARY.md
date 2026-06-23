---
phase: 04-agent-architecture-cost-bounds
plan: 01
subsystem: test-infrastructure
tags: [wave-0, tdd, stubs, cost-ceiling, ledger, pricing, spend-route, suspicious-content, settings, ast-gate]
dependency_graph:
  requires: []
  provides:
    - Wave-0 RED stub files for all 11 VALIDATION.md rows
    - D-05 AST regression gate (Decision never Haiku)
  affects:
    - tests/unit/test_cost_ceiling.py
    - tests/unit/test_cost_ledger.py
    - tests/unit/test_pricing.py
    - tests/unit/test_spend_route.py
    - tests/unit/test_settings_route.py
    - tests/unit/test_suspicious_content.py
    - tests/unit/test_decision_prompt_isolation.py
tech_stack:
  added: []
  patterns:
    - Wave-0 stub pattern (module-level ImportError for not-yet-built symbols)
    - NotImplementedError stubs for wired-but-not-implemented logic
    - AST gate pattern (D-05 mirror of existing isolation gates)
key_files:
  created:
    - tests/unit/test_cost_ceiling.py
    - tests/unit/test_cost_ledger.py
    - tests/unit/test_pricing.py
    - tests/unit/test_spend_route.py
    - tests/unit/test_settings_route.py
    - tests/unit/test_suspicious_content.py
  modified:
    - tests/unit/test_decision_prompt_isolation.py
decisions:
  - "D-05 AST gate implemented as test_decision_never_haiku_model — passes GREEN immediately (no existing Haiku violation); confirms regression-prevention intent."
  - "test_cost_ledger.py imports existing audit symbols (append_event, normalize_decimals) so it collects cleanly; stubs raise NotImplementedError to stay RED."
  - "test_suspicious_content.py imports _INJECTION_PATTERNS from gekko.agent.runtime (not yet defined) so it fails ImportError — correct RED signal."
  - "test_spend_route.py test_spend_get_returns_200 and test_spend_get_requires_auth are semi-live (test real app routing) and fail 404 until /spend route ships — RED as expected."
  - "test_settings_route.py created as NEW file (did not previously exist) with 2 stubs."
metrics:
  duration: "10 minutes"
  completed: "2026-06-23"
  tasks_completed: 2
  tasks_total: 2
  files_created: 6
  files_modified: 1
---

# Phase 04 Plan 01: Wave-0 Test Scaffolding (Nyquist Stubs) Summary

**One-liner:** 6 new RED stub test files + D-05 "Decision never Haiku" AST gate appended to test_decision_prompt_isolation.py, covering all 11 VALIDATION.md rows before any implementation begins.

## What Was Built

### Task 1: Cost ceiling, ledger, and pricing stubs (COST-01/COST-04/COST-05)

**`tests/unit/test_cost_ceiling.py`** — 8 stubs:
- `test_80pct_threshold_triggers_degrade` — asserts `CeilingCheck.action == "degrade"` at 80% spend
- `test_100pct_threshold_triggers_halt` — asserts `CeilingCheck.action == "halt"` at 100% spend
- `test_halt_returns_skipped` — asserts `trigger_strategy_run` returns `outcome="skipped_cost_halt"` when ceiling halts
- `test_tz_midnight_reset` — asserts yesterday-UTC spend reads as zero in America/New_York tz (NotImplementedError stub)
- `test_single_dm_80` — asserts no repeat DM when `cost_alert_80_sent_date == today` (NotImplementedError stub)
- `test_single_dm_100` — same for 100% guard (NotImplementedError stub)
- `test_triage_gate_skips` — asserts `outcome == "triage_skipped"` in degraded mode (NotImplementedError stub)
- `test_allow_when_below_80pct` — asserts `action == "allow"` at 50% spend

**Collection status:** ImportError on collect (module `gekko.agent.cost_ceiling` not yet built) — correct RED.

**`tests/unit/test_cost_ledger.py`** — 4 stubs:
- `test_llm_cost_event_written_per_researcher_query` — NotImplementedError stub
- `test_cost_usd_is_decimal_not_float` — NotImplementedError stub
- `test_normalize_decimals_called` — NotImplementedError stub
- `test_none_total_cost_usd_defaults_to_zero` — partial: verifies `Decimal(str(None or 0.0)) == Decimal("0")` (pattern check), then raises NotImplementedError for full integration

**Collection status:** 4 tests collected (imports existing audit symbols). All 4 fail with NotImplementedError — correct RED.

**`tests/unit/test_pricing.py`** — 7 stubs:
- 4 constant tests: `SONNET_INPUT_PER_MTOK == Decimal("3.00")`, `SONNET_OUTPUT_PER_MTOK == Decimal("15.00")`, `HAIKU_INPUT_PER_MTOK == Decimal("1.00")`, `HAIKU_OUTPUT_PER_MTOK == Decimal("5.00")`
- 2 formula tests: `tokens_to_usd(1_000_000, 0, model="sonnet") == Decimal("3.00")`, same for haiku
- 1 default: `DEFAULT_DAILY_CEILING_USD == Decimal("5.00")`

**Collection status:** ImportError on collect (module `gekko.agent.pricing` not yet built) — correct RED.

### Task 2: Spend route, suspicious content, settings extension + D-05 AST gate (COST-02/COST-03/SC-2/D-05)

**`tests/unit/test_spend_route.py`** — 6 stubs:
- `test_spend_get_returns_200` — semi-live: logs in, GETs `/spend`, asserts 200; currently fails 404 (route not built)
- `test_spend_get_shows_today_total`, `test_spend_get_shows_ceiling`, `test_spend_get_per_strategy_breakdown`, `test_spend_get_7day_history` — NotImplementedError stubs
- `test_spend_get_requires_auth` — semi-live: GETs `/spend` without auth, asserts 302 to /login; currently fails 404

**Collection status:** 6 tests collected; all fail RED (404 or NotImplementedError).

**`tests/unit/test_suspicious_content.py`** — 4 stubs:
- `test_injection_pattern_triggers_event` — verifies pattern fires, then NotImplementedError for full integration wiring
- `test_override_pattern_triggers_event` — same
- `test_clean_content_no_event` — verifies pattern does NOT fire, then NotImplementedError
- `test_payload_contains_source_info` — NotImplementedError stub

**Collection status:** ImportError on collect (`_INJECTION_PATTERNS` not in `gekko.agent.runtime`) — correct RED.

**`tests/unit/test_settings_route.py`** (NEW file) — 2 stubs:
- `test_ceiling_saved` — NotImplementedError stub
- `test_ceiling_defaults_to_5` — NotImplementedError stub

**Collection status:** 2 tests collected; both fail with NotImplementedError — correct RED.

**`tests/unit/test_decision_prompt_isolation.py`** (EXTENDED, not replaced) — 1 gate added:
- `test_decision_never_haiku_model` — D-05 AST gate; walks all `*.py` under `src/gekko/agent/`; asserts no `_run_decision`/`build_decision_prompt`/`_invoke_decision`/`decision_prompt_*` function passes `model="haiku"` as a keyword argument

**Verification:** Passes GREEN immediately (no existing Haiku in Decision path). All 7 tests in the file remain GREEN.

## Verification Results

```
.venv/Scripts/python.exe -m pytest tests/unit/test_decision_prompt_isolation.py -x -q
7 passed in 1.28s  ← all GREEN including new D-05 gate

pytest collect for 6 new stub files:
  - test_cost_ceiling.py: ERROR (ImportError — gekko.agent.cost_ceiling) ← RED as expected
  - test_pricing.py: ERROR (ImportError — gekko.agent.pricing) ← RED as expected
  - test_suspicious_content.py: ERROR (ImportError — _INJECTION_PATTERNS) ← RED as expected
  - test_cost_ledger.py: 4 tests collected (imports existing audit symbols)
  - test_spend_route.py: 6 tests collected (imports existing dashboard app)
  - test_settings_route.py: 2 tests collected
```

## Deviations from Plan

None — plan executed exactly as written.

- `test_settings_route.py` was not a pre-existing file (plan said "extend existing file" but the file did not yet exist). Created as a NEW file with the 2 COST-03 stubs — this is not a deviation from intent; the plan's "extend" instruction assumed the file existed but it had not been created yet.

## Known Stubs

All 6 new files are intentional stubs (Wave-0 Nyquist requirement). The following stubs are by design and will be turned GREEN in Waves 2-5:

| File | Stub | Wave |
|------|------|------|
| test_cost_ceiling.py | test_tz_midnight_reset | Wave 2 |
| test_cost_ceiling.py | test_single_dm_80 | Wave 2 |
| test_cost_ceiling.py | test_single_dm_100 | Wave 2 |
| test_cost_ceiling.py | test_triage_gate_skips | Wave 2 |
| test_cost_ledger.py | all 4 tests | Wave 2 |
| test_pricing.py | all 7 tests | Wave 2 |
| test_spend_route.py | all 6 tests | Wave 3 |
| test_settings_route.py | all 2 tests | Wave 3 |
| test_suspicious_content.py | all 4 tests | Wave 2 |

## Threat Surface Scan

No new production symbols introduced in this plan (test-only changes). No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries. The D-05 AST gate is purely a test-time assertion over existing source files — no production execution path.

## Self-Check: PASSED

Files exist:
- tests/unit/test_cost_ceiling.py: FOUND
- tests/unit/test_cost_ledger.py: FOUND
- tests/unit/test_pricing.py: FOUND
- tests/unit/test_spend_route.py: FOUND
- tests/unit/test_settings_route.py: FOUND
- tests/unit/test_suspicious_content.py: FOUND
- tests/unit/test_decision_prompt_isolation.py: FOUND (extended)

Commits exist:
- 07c980f: test(04-01): Wave-0 RED stubs — cost ceiling, ledger, pricing
- 98f4d2c: test(04-01): Wave-0 RED stubs — spend route, suspicious content, settings + D-05 AST gate
