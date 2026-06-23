---
phase: 04-agent-architecture-cost-bounds
plan: "04"
subsystem: agent-runtime
tags:
  - cost-tracking
  - degradation
  - triage
  - scheduler
  - ledger
dependency_graph:
  requires:
    - "04-01"
    - "04-02"
    - "04-03"
  provides:
    - llm_cost_ledger_per_query
    - haiku_triage_gate
    - context_trim_degradation
    - cadence_x2_reschedule
  affects:
    - src/gekko/agent/runtime.py
    - src/gekko/agent/researcher.py
    - src/gekko/scheduler/jobs.py
tech_stack:
  added: []
  patterns:
    - ResultMessage capture in async-for query() stream
    - SDKResultMessage isinstance check + Decimal(str(float)) money conversion
    - Haiku-model triage query with allowed_tools=[] in degradation path only
    - APScheduler 3.x reschedule_job() for cadence x2 (not remove+add)
key_files:
  created: []
  modified:
    - src/gekko/agent/runtime.py
    - src/gekko/agent/researcher.py
    - src/gekko/scheduler/jobs.py
    - tests/unit/test_cost_ledger.py
    - tests/unit/test_cost_ceiling.py
decisions:
  - "Triage gate in trigger_strategy_run, NOT in _run_decision, preserving D-05 AST invariant"
  - "BudgetTracker(soft_max_calls=6) in degradation mode (D-04 tactic 3 — context trim)"
  - "Triage llm_cost event uses strategy_id=None (strategy not loaded at triage point)"
  - "APScheduler 3.11.2 reschedule_job() used (not 4.x API) — confirmed installed version"
  - "ResultMessage.result carries brief text but runtime reads AssistantMessage TextBlock content"
metrics:
  duration: "45 minutes"
  completed: "2026-06-23"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 5
---

# Phase 4 Plan 4: Per-LLM-Call Cost Ledger + Haiku Triage Gate + Cadence x2 Summary

Wave 4 wires the cost-tracking pipeline end-to-end: `ResultMessage.total_cost_usd` → Decimal conversion → `llm_cost` audit event, Haiku pre-triage in degrade mode, context-trim via reduced `BudgetTracker` soft cap and `max_evidence_items=3`, and APScheduler 3.x cadence-x2 reschedule functions.

## What Was Built

### Task 1: Cost Ledger Writes (COST-05)

**`src/gekko/agent/runtime.py`**

- Imported `SDKResultMessage` from `claude_agent_sdk.types` and `Decimal` from stdlib
- Added `result_msg: SDKResultMessage | None`, `input_tokens: int`, `output_tokens: int` accumulators before both `query()` loops
- Inside each loop: `isinstance(msg, SDKResultMessage)` captures the final ResultMessage; `isinstance(msg, AssistantMessage)` accumulates token counts from `msg.usage`
- After each loop: `cost_usd = Decimal(str(result_msg.total_cost_usd or 0.0))` — never stores float directly
- Writes `llm_cost` audit event via `append_event` + `normalize_decimals` with payload: `{run_id, strategy_name, model, call_type, input_tokens, output_tokens, cost_usd}`
- `call_type` = `"researcher"` | `"decision"` distinguishes the two calls

**Signature changes:**
- `_run_researcher`: new params `session_factory`, `strategy_db_id`, `max_turns` (default 12), `max_evidence_items` (default None)
- `_run_decision`: new params `session_factory`, `user_id`, `run_id`, `strategy_db_id`, `strategy_name`
- `trigger_strategy_run`: passes all new params; computes `_researcher_max_turns = 6 if _degradation_mode else _RESEARCHER_MAX_TURNS` and `_researcher_max_evidence = 3 if _degradation_mode else None`

**`src/gekko/agent/researcher.py`**

- `build_researcher_prompt`: added optional `max_evidence_items: int | None = None` param
- When set (degradation mode), appends `[DEGRADED MODE] Limit evidence items to N maximum` to guidance block

**`tests/unit/test_cost_ledger.py`**

- Replaced all 4 `NotImplementedError` stubs with real tests
- 3 pure math tests (Decimal conversion, normalize_decimals, None fallback) — GREEN immediately
- 2 integration tests using real SDK types (`SDKResultMessage`, `AssistantMessage`, `TextBlock`, `ToolUseBlock`) with patched `query()` and `append_event`
- Asserts `event_type="llm_cost"`, `call_type` field, and `isinstance(cost_usd, Decimal)`

### Task 2: Haiku Triage Gate + Context-Trim + Cadence x2

**`src/gekko/agent/runtime.py` — triage gate block**

Inserted between session factory setup and `BudgetTracker` construction in `trigger_strategy_run`:

```
if _degradation_mode:
    # run Haiku pre-triage query (model="haiku", max_turns=1, allowed_tools=[])
    # collect triage_text from AssistantMessage TextBlocks
    # write triage llm_cost event (call_type="triage", model="haiku")
    if "NO" in triage_text.upper():
        return {"run_id": ..., "outcome": "triage_skipped", "source": source}
```

D-05 invariant preserved: `model="haiku"` appears **only** in `trigger_strategy_run` (triage block), never in `_run_decision` or `build_decision_prompt`. The AST gate `test_decision_never_haiku_model` checks only `_run_decision`-pattern functions and remains GREEN.

**`BudgetTracker` context-trim (D-04 tactic 3):**
Changed from `BudgetTracker()` to `BudgetTracker(soft_max_calls=6 if _degradation_mode else 12)`.

**`src/gekko/scheduler/jobs.py`**

Added two new functions:
- `reschedule_strategy_degraded(scheduler, *, user_id, strategy_name, original_schedule_time) -> str`: parses original HH:MM, shifts +12h (mod 24), calls `scheduler.reschedule_job(job_id, trigger=CronTrigger(hour=degraded_hh, ...))` — APScheduler 3.x API
- `restore_strategy_normal_cadence(scheduler, *, user_id, strategy_name, schedule_time) -> str`: delegates to `schedule_strategy_daily(scheduler, ...)` with `replace_existing=True`

Both added to `__all__`.

**`tests/unit/test_cost_ceiling.py`**

- `test_triage_gate_skips`: replaced Wave 3 stub with full Wave 4 test; uses real SDK `AssistantMessage(content=[TextBlock("NO")], ...)` + real `SDKResultMessage(total_cost_usd=0.0002)`; patches `query()` and `append_event`; asserts `outcome="triage_skipped"`, exactly 1 `query()` call, 1 `llm_cost` event with `call_type="triage"`

## Verification Results

```
tests/unit/test_cost_ceiling.py    8 tests  GREEN
tests/unit/test_cost_ledger.py     5 tests  GREEN
tests/unit/test_decision_prompt_isolation.py  7 tests  GREEN (D-05 AST gate GREEN)
```

Full unit suite (730 passed, excluding 15 pre-existing failures in other plans):
- `test_approval_proposals.py::test_handle_edit_size_stub_acks_and_opens_modal` — pre-existing D-62 stub
- `test_settings_route.py` (2) — Wave 5 scope stubs
- `test_spend_route.py` (6) — Wave 5 scope stubs
- `test_research_tools.py::test_finnhub_news_degrades_gracefully_without_key` — requires FINNHUB_API_KEY env var

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Functionality] `build_researcher_prompt` lacked `max_evidence_items` parameter**
- **Found during:** Task 1 implementation (plan referenced it but function didn't have it)
- **Fix:** Added `max_evidence_items: int | None = None` optional parameter with backward-compatible default; appends `[DEGRADED MODE]` guidance note when set
- **Files modified:** `src/gekko/agent/researcher.py`
- **Commit:** 45cd983

**2. [Rule 1 - Bug] Test mock used `MagicMock(spec=AssistantMessage)` which doesn't pass real `isinstance` checks**
- **Found during:** Task 1 test development
- **Fix:** Used real SDK constructors (`AssistantMessage(content=[TextBlock(...)], model=..., usage=...)`, `SDKResultMessage(subtype=..., duration_ms=..., ...)`) so stdlib `isinstance()` passes without patching builtins
- **Files modified:** `tests/unit/test_cost_ledger.py`
- **Commit:** 45cd983

**3. [Rule 1 - Bug] `SDKResultMessage` missing required positional args (`duration_ms`, `duration_api_ms`, `session_id`)**
- **Found during:** Task 1 test execution
- **Fix:** Added all required fields to `SDKResultMessage(...)` constructor calls in tests
- **Files modified:** `tests/unit/test_cost_ledger.py`
- **Commit:** 45cd983

## Known Stubs

None — all plan tasks fully implemented. Wave 5 stubs in `test_settings_route.py` and `test_spend_route.py` are out of scope for this plan.

## Threat Flags

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The triage gate's `model="haiku"` is audited by the D-05 AST gate. The `Decimal(str(float))` conversion for `total_cost_usd` addresses T-04-11 (float-to-Decimal tampering).

## Self-Check: PASSED

- `src/gekko/agent/runtime.py` — modified with SDKResultMessage import, triage gate, cost ledger writes
- `src/gekko/agent/researcher.py` — modified with `max_evidence_items` param
- `src/gekko/scheduler/jobs.py` — modified with `reschedule_strategy_degraded` + `restore_strategy_normal_cadence`
- `tests/unit/test_cost_ledger.py` — 5 tests GREEN
- `tests/unit/test_cost_ceiling.py` — 8 tests GREEN (including updated `test_triage_gate_skips`)
- Commits: 45cd983, 2b41391
