---
phase: 04-agent-architecture-cost-bounds
plan: 06
subsystem: dashboard
tags: [cost-bounds, spend-display, payload-unwrap, regression-gate, tdd]
gap_closure: true
requirements_closed: [COST-02, COST-05]

dependency_graph:
  requires: ["04-05"]
  provides: ["SC-5 gap closure: /spend shows real cost data from canonical-wrapped llm_cost events"]
  affects: ["src/gekko/dashboard/routes.py", "tests/unit/test_spend_route.py"]

tech_stack:
  added: []
  patterns:
    - "canonical-payload unwrap: inner = payload.get('payload', payload) mirrors cost_ceiling.py line 202"
    - "Decimal cost accumulation from canonical-wrapper shape; no float arithmetic"

key_files:
  modified:
    - path: "src/gekko/dashboard/routes.py"
      role: "spend_get: added inner = payload.get('payload', payload) in today_rows loop (line ~1306) and history_rows loop (line ~1342)"
    - path: "tests/unit/test_spend_route.py"
      role: "_make_llm_cost_row updated to canonical wrapper shape; test_spend_get_canonical_payload_unwrap added as regression gate"

decisions:
  - "Unwrap pattern mirrors cost_ceiling.py line 202 exactly: inner = payload.get('payload', payload) then inner.get('cost_usd', '0'). The .get fallback to payload tolerates legacy-flat rows without KeyError, ensuring backward compatibility."
  - "_make_llm_cost_row now emits the full canonical-subset string matching what append_event stores. The outer event_type/ts/user_id keys are present at the top level; cost fields are nested inside 'payload'."

metrics:
  duration: "12min"
  completed_date: "2026-06-24"
  tasks_completed: 3
  files_modified: 2
  commits: ["20af238", "d11d48a"]
---

# Phase 04 Plan 06: Canonical Payload Unwrap in spend_get Summary

**One-liner:** Fixed spend_get canonical-payload nesting bug (`inner = payload.get("payload", payload)` in both today + history loops) and hardened test_spend_route.py to exercise the real canonical wrapper shape, closing SC-5 gap.

## What Was Built

This plan closed the SC-5 / COST-02 gap identified in 04-VERIFICATION.md: the `/spend` dashboard route displayed $0.00 for all cost totals and "Unknown" for all strategy names because `spend_get` in `routes.py` read `cost_usd` and `strategy_name` at the top level of the canonical event JSON. However, `append_event` (audit/log.py) stores the full canonical subset as `payload_json`:

```json
{"event_type":"llm_cost","payload":{"cost_usd":"0.05","strategy_name":"strat-a",...},"ts":"...","user_id":"..."}
```

The actual cost fields live inside the nested `"payload"` key. `cost_ceiling.py` already handled this correctly with `inner = payload.get("payload", payload)` (line 202). `routes.py` did not — until this fix.

### routes.py Fix (Task 1)

Two two-line insertions in `spend_get`:

**Today-rows loop (~line 1306):**
```python
payload = json.loads(row.payload_json)
inner = payload.get("payload", payload)  # NEW — unwrap canonical wrapper
cost = Decimal(str(inner.get("cost_usd", "0")))     # was payload.get(...)
strat_name = str(inner.get("strategy_name", "Unknown"))  # was payload.get(...)
```

**History-rows loop (~line 1342):**
```python
payload = json.loads(row.payload_json)
inner = payload.get("payload", payload)  # NEW — unwrap canonical wrapper
cost = Decimal(str(inner.get("cost_usd", "0")))     # was payload.get(...)
```

The `.get("payload", payload)` fallback tolerates legacy-flat rows: if no nested `"payload"` key exists, `inner` equals `payload` (the outer dict), so old rows are read as-is without KeyError.

### test_spend_route.py Hardening (Task 2)

`_make_llm_cost_row` updated to emit the canonical wrapper shape:
```python
json.dumps({
    "event_type": "llm_cost",
    "payload": {
        "cost_usd": cost_usd,
        "strategy_name": strategy_name,
        "model": "sonnet",
        "call_type": "researcher",
        "input_tokens": 100,
        "output_tokens": 50,
    },
    "ts": ts,
    "user_id": "testuser",
})
```

New test `test_spend_get_canonical_payload_unwrap` added as a regression gate. It feeds two canonical-wrapped rows (cost_usd="0.05"/"strat-a", cost_usd="0.03"/"strat-b") and asserts:
- `"0.08"` or `"0.0800"` appears in response (today_total = $0.08)
- `"strat-a"` appears in response (per-strategy breakdown)
- `"strat-b"` appears in response (per-strategy breakdown)

This test would have **failed** against the pre-fix top-level-read code (always $0.00, always "Unknown") and **passes** after the unwrap fix.

### Regression Suite (Task 3)

All three required suites confirmed green:
- `test_cost_ceiling.py`: 8/8 passed
- `test_decision_prompt_isolation.py`: 6/6 passed
- `test_dashboard_templates_sri.py`: 4/4 passed

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix spend_get canonical-payload unwrap in routes.py | 20af238 | src/gekko/dashboard/routes.py |
| 2 | Harden test_spend_route.py — canonical-wrapper rows + regression gate | d11d48a | tests/unit/test_spend_route.py |
| 3 | Confirm existing green suites remain green | (no commit — verification only) | — |

## Deviations from Plan

None — plan executed exactly as written. Both two-line insertions applied in the specified loops. Test helper updated to canonical shape. Regression gate added.

## Known Stubs

None. The fix wires real data flow end-to-end: canonical rows in DB → unwrap in spend_get → real cost totals and strategy names in the /spend template.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The unwrap is a pure Python source edit inside an existing authenticated route. Threat register items T-04-06-01 (cross-user isolation), T-04-06-02 (Decimal precision), and T-04-06-03 (strategy_name scoping) all confirmed unchanged — auth gating and user_id DB filtering untouched.

## Self-Check

Files created/modified:
- [x] `src/gekko/dashboard/routes.py` — FOUND (contains `inner = payload.get` at lines 1306 and 1342)
- [x] `tests/unit/test_spend_route.py` — FOUND (7 tests pass; contains `canonical` in docstrings)

Commits:
- [x] `20af238` — FOUND
- [x] `d11d48a` — FOUND

## Self-Check: PASSED
