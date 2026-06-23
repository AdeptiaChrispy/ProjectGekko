---
phase: 04-agent-architecture-cost-bounds
verified: 2026-06-23T00:00:00Z
status: gaps_found
score: 4/5 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Dashboard shows spend per strategy and per user with the daily ceiling visible (SC-5)"
    status: failed
    reason: "spend_get in routes.py reads cost_usd at the top level of the parsed payload_json (payload.get('cost_usd', '0')), but append_event stores the full canonical subset {'event_type':..., 'payload':{...actual fields...}, 'ts':..., 'user_id':...}. The actual cost_usd lives one level deeper inside the nested 'payload' key. cost_ceiling.py correctly handles this with inner = payload.get('payload', payload), but routes.py spend_get does not. In production every llm_cost row would decode to cost=$0. Unit tests pass because _make_llm_cost_row mocks flat JSON that bypasses the canonical wrapper."
    artifacts:
      - path: "src/gekko/dashboard/routes.py"
        issue: "Lines 1301-1302 and 1335-1336: json.loads(row.payload_json) then payload.get('cost_usd', '0') — misses the nested 'payload' dict in the canonical event format. Both today_total computation and 7-day history bucket loop have the same bug."
    missing:
      - "Add inner = payload.get('payload', payload) unwrap in spend_get before calling inner.get('cost_usd', '0') and inner.get('strategy_name', 'Unknown') — mirroring the exact pattern already in cost_ceiling.py lines 202-204. Apply to both the today_rows loop (lines 1299-1310) and the history_rows loop (lines 1333-1348)."
      - "Update test_spend_route.py _make_llm_cost_row to use the canonical wrapper format so the test actually exercises the production parse path (optional but strongly recommended to prevent future regressions)."
human_verification:
  - test: "Run agent cycles until real LLM cost events are written, then navigate /spend in a browser"
    expected: "Today's spend total matches the sum of llm_cost event cost_usd values; per-strategy breakdown shows actual strategy names and spend; 7-day history table has rows with non-zero spend for days with cycles"
    why_human: "Requires live Claude API calls, a running ASGI stack, and a browser to visually confirm the spend bars and tables render with real data"
  - test: "Set daily_cost_ceiling_usd to a small value (e.g. $0.10) and run cycles until 80% and then 100% are reached"
    expected: "Exactly one Slack DM at the 80% crossing; exactly one Slack DM at the 100% crossing; subsequent skipped cycles do not send additional DMs; agent resumes after raising the ceiling in Settings"
    why_human: "Requires real Slack workspace + real Claude API spend + clock time to cross thresholds; cannot be automated without live credentials"
  - test: "Observe the tz-midnight ceiling reset"
    expected: "The running spend total resets to $0.00 at the user-configured timezone midnight; agent that was halted resumes on the first cycle after reset"
    why_human: "Requires wall-clock day boundary; cannot be accelerated in unit tests"
  - test: "Apply Alembic migration 0005 to the live populated DB (currently at 0004)"
    expected: "alembic upgrade head completes without error; users table gains daily_cost_ceiling_usd (TEXT, server_default '5.00'), cost_alert_80_sent_date, cost_alert_100_sent_date; events ck_event_type accepts llm_cost and suspicious_content; existing data unchanged"
    why_human: "Live DB migration is an operator step requiring passphrase + manual backup; cannot be run in CI against the populated production DB"
---

# Phase 4: Agent Architecture & Cost Bounds Verification Report

**Phase Goal:** Agent operates with research/decision separation, prompt-injection defense, bounded research turns, and a two-tier cost ceiling (80% graceful degradation, 100% hard halt) the agent cannot talk past, with a per-LLM-call cost ledger and dashboard spend view.
**Verified:** 2026-06-23
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC-1: Researcher subagent has read-only tools only; Decision subagent consumes only a structured brief; no shared raw context; structured tool-use schema enforcement | VERIFIED | `RESEARCHER_TOOLS` in researcher.py contains only 4 read-only tools; `DECISION_TOOLS` in decision.py contains exactly 2 proposal tools; `_run_researcher` and `_run_decision` use separate `query()` calls; brief is parsed by Python before crossing to Decision; D-40 trust-boundary block present in decision prompt; `test_decision_prompt_isolation.py` AST isolation gate GREEN |
| 2 | SC-2: Prompt-injection neutralized + suspicious_content audit event logged | VERIFIED | `_INJECTION_PATTERNS` compiled at runtime.py module level (5 patterns, re.IGNORECASE); scan runs between `_run_researcher` and `_run_decision`; `append_event(..., event_type="suspicious_content")` wired at lines 828-842; `injected_content_flags: list[str] = []` added to `ResearchBrief` in schemas/research.py; executor.py `_BYPASS_CATEGORIES` includes `"cost_alert"`; test_suspicious_content.py 4/4 GREEN |
| 3 | SC-3: Research turns bounded; no_action as first-class output | VERIFIED | `BudgetTracker(soft_max_calls=6 if _degradation_mode else 12)` at runtime.py line 789; `_RESEARCHER_MAX_TURNS = 12` constant; `_researcher_max_turns = 6 if _degradation_mode else _RESEARCHER_MAX_TURNS` at line 805; `propose_no_action` is one of exactly two Decision tools; unchanged from Phase 1 — test_cost_ceiling.py 8/8 GREEN covers the budget-related tests |
| 4 | SC-4: 80% → degrade + Slack DM; 100% → hard-halt + Slack DM; tz-midnight reset; single-DM guards; Haiku triage gate | VERIFIED | `check_cost_ceiling()` in cost_ceiling.py is deterministic (no LLM calls), fires before any `query()` at runtime.py line 628 (after quiet-hours gate, before session_factory construction); `just_crossed_80/just_crossed_100` flags update `cost_alert_*_sent_date` columns on first cross only; Slack DM calls at lines 641-654 (halt) and 671-685 (degrade) via `_send_slack_dm_respecting_quiet_hours(..., category="cost_alert")`; Haiku triage gate (lines 715-787) fires in `_degradation_mode` only with `model="haiku"`, `max_turns=1`, `allowed_tools=[]`; "NO" response returns `outcome="triage_skipped"`; D-05 AST gate GREEN (model="haiku" absent from `_run_decision`/`build_decision_prompt`); `reschedule_strategy_degraded()` and `restore_strategy_normal_cadence()` in jobs.py |
| 5 | SC-5: Every LLM call logged to cost ledger (input_tokens, output_tokens, USD Decimal); dashboard shows spend per strategy + per user with ceiling visible | FAILED | Ledger writes VERIFIED: `_run_researcher`, `_run_decision`, and triage gate each write `llm_cost` events with `call_type`, `input_tokens`, `output_tokens`, `cost_usd` (Decimal via `Decimal(str(total_cost_usd or 0.0))`), `normalize_decimals` applied. Dashboard FAILED: `spend_get` in routes.py parses `payload.get("cost_usd", "0")` at the top level of the canonical event JSON, but `append_event` stores the full canonical subset `{"event_type":...,"payload":{...actual fields...},"ts":...,"user_id":...}`. The cost_usd lives in the nested "payload" dict. `cost_ceiling.py` handles this correctly with `inner = payload.get("payload", payload)` (line 202). `routes.py` does not. In production the spend dashboard would always show $0.00 for today_total, $0.00 per strategy, and $0.00 for every 7-day history bucket. Tests pass because `_make_llm_cost_row` mocks flat JSON that bypasses the canonical wrapper format. |

**Score:** 4/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/gekko/agent/cost_ceiling.py` | CeilingCheck dataclass + check_cost_ceiling() deterministic gate | VERIFIED | Exists, substantive (257 lines), wired: imported and called at runtime.py line 66 (import) and line 628 (call) |
| `src/gekko/agent/pricing.py` | Pricing constants (Decimal) + tokens_to_usd() | VERIFIED | Exists; SONNET $3/$15, HAIKU $1/$5 per MTok; DEFAULT_DAILY_CEILING_USD=$5.00; all exported in `__all__`; test_pricing.py 7/7 GREEN |
| `migrations/versions/0005_p4_cost_ceiling.py` | Alembic 0005 migration: 3 users columns + 2 event_types | VERIFIED | Exists; `down_revision = "0004_p3_hitl_ux"`; users.daily_cost_ceiling_usd, cost_alert_80_sent_date, cost_alert_100_sent_date added; ck_event_type extended with llm_cost and suspicious_content; upgrade + downgrade both present |
| `src/gekko/db/models.py` | User ORM extended; _EVENT_TYPES extended | VERIFIED | 3 new Mapped columns at lines 212-214; `_EVENT_TYPES` tuple includes "llm_cost" and "suspicious_content" at lines 120-121 |
| `src/gekko/agent/runtime.py` | Ceiling gate + SC-2 scan + ledger writes | VERIFIED | _INJECTION_PATTERNS at lines 154-158; ceiling gate at lines 623-688; SC-2 scan at lines 825-845; llm_cost writes in _run_researcher (lines 362-387) and _run_decision (lines 466-492); triage gate at lines 711-787 |
| `src/gekko/execution/executor.py` | cost_alert in _BYPASS_CATEGORIES | VERIFIED | Line 253: `"cost_alert"` in frozenset with comment referencing D-07/D-08 |
| `src/gekko/scheduler/jobs.py` | reschedule_strategy_degraded() + restore_strategy_normal_cadence() | VERIFIED | Both functions present at lines 253 and 297 respectively; exported in `__all__` |
| `src/gekko/dashboard/routes.py` | GET /spend on auth-gated router; settings ceiling field | PARTIALLY VERIFIED | GET /spend route exists at line 1228 on auth-gated `@router`; settings_get/settings_post extended with `daily_cost_ceiling_usd`; ceiling field saves and loads correctly; BUT spend_get has the nested-payload parsing bug that causes $0.00 spend in production |
| `src/gekko/dashboard/templates/spend.html.j2` | Spend view: today vs ceiling, per-strategy, 7-day | VERIFIED | Exists; extends base.html.j2; no external scripts; progress bar with color tiers; per-strategy table; 7-day history table |
| `src/gekko/dashboard/templates/settings.html.j2` | Daily ceiling fieldset inside existing form | VERIFIED | ceiling fieldset present with input type=number, step=0.01, min=0.50, placeholder from context |
| `src/gekko/dashboard/templates/base.html.j2` | Spend nav link | VERIFIED | Line 57: `<a href="/spend">Spend</a>` present in nav |
| `src/gekko/schemas/research.py` | injected_content_flags field on ResearchBrief | VERIFIED | `injected_content_flags: list[str] = Field(default_factory=list)` at line 140 |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| runtime.py | cost_ceiling.py | `await check_cost_ceiling(session_factory=..., user_id=...)` | WIRED | Lines 66 (import) + 628 (call) |
| runtime.py | audit/log.py | `append_event(..., event_type="llm_cost")` | WIRED | Lines 362-387 (_run_researcher), 466-492 (_run_decision), 757-766 (triage) |
| runtime.py | audit/log.py | `append_event(..., event_type="suspicious_content")` | WIRED | Lines 828-843 (SC-2 scan) |
| cost_ceiling.py | db/models.py | `Event.event_type == "llm_cost"` | WIRED | Line 186 select filter |
| routes.py spend_get | db/models.py Event | `select Event where event_type="llm_cost"` | WIRED (query only) | Lines 1284-1291 query fires; but payload parsing is broken — WIRED but data-disconnected |
| routes.py | pricing.py | `DEFAULT_DAILY_CEILING_USD` | WIRED | Line 1248 import |
| migrations/0005 | models.py | frozen vocabulary copy | WIRED | _FROZEN_EVENT_TYPES_POST matches _EVENT_TYPES in models.py |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| spend.html.j2 today_total | `today_total` Decimal | routes.py spend_get → Event rows → `payload.get("cost_usd", "0")` | NO — top-level key not found in canonical event; always $0.00 in production | HOLLOW — wired but data disconnected due to payload nesting bug |
| spend.html.j2 by_strategy | `by_strategy` list | routes.py spend_get → `payload.get("strategy_name", "Unknown")` | NO — same nesting issue; all rows show "Unknown" / $0.00 | HOLLOW — same bug |
| spend.html.j2 history | `history` 7-day buckets | routes.py spend_get → history_rows → `payload.get("cost_usd", "0")` | NO — same nesting issue; all days show $0.00 | HOLLOW — same bug |
| spend.html.j2 ceiling | `ceiling` Decimal | routes.py spend_get → User.daily_cost_ceiling_usd | YES — loaded directly from User column, no payload parsing involved | FLOWING |
| cost_ceiling.py CeilingCheck | `current_spend` Decimal | check_cost_ceiling → Event rows → `inner = payload.get("payload", payload)` then `inner.get("cost_usd", "0")` | YES — correctly unwraps canonical wrapper | FLOWING |

**Root cause of data-flow failure:** `append_event` stores `payload_json` as the full canonical subset `{"event_type":"llm_cost","payload":{...cost fields...},"ts":"...","user_id":"..."}`. The actual `cost_usd` and `strategy_name` live inside the nested `"payload"` key. `cost_ceiling.py` correctly unwraps this with `inner = payload.get("payload", payload)` (lines 202-204). `routes.py` spend_get does not apply this unwrap, so all cost reads return the default "0". The ceiling guard enforces correctly; the dashboard surface shows zeros.

---

## Behavioral Spot-Checks

Step 7b: SKIPPED — no runnable entry points without a live SQLCipher DB + Claude API credentials. The ceiling gate and spend route are exercised by unit tests, not standalone CLI commands.

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| COST-01 | 04-02, 04-03, 04-04 | Per-user pooled daily ceiling; configurable; tz-midnight reset; 80%/100% tiers; halt before LLM | SATISFIED | check_cost_ceiling() deterministic gate wired at runtime.py:628; D-01/D-02/D-03 honored; test_cost_ceiling 8/8 GREEN |
| COST-02 | 04-05 | Dashboard spend view: today total vs ceiling, per-strategy breakdown, 7-day history | BLOCKED | GET /spend route exists and is auth-gated; ceiling display works; but today_total/by_strategy/history all show $0.00 in production due to canonical-payload nesting bug in routes.py |
| COST-03 | 04-05 | Configurable ceiling in Settings; Decimal validation; saves to users row | SATISFIED | settings_get passes ceiling_value to template; settings_post accepts Form field, validates Decimal > 0, writes normalized string; test_settings_route 2/2 ceiling tests GREEN |
| COST-04 | 04-03, 04-04 | 80% degrade (cadence x2, Haiku triage, context trim); 100% halt; one DM each; D-05 Decision never Haiku | SATISFIED | All three degradation tactics implemented; single-DM guards via cost_alert_*_sent_date; Haiku confined to trigger_strategy_run triage gate; AST gate GREEN; test_cost_ceiling 8/8 GREEN |
| COST-05 | 04-04 | Every LLM call logged: input_tokens, output_tokens, USD Decimal | PARTIALLY SATISFIED | Ledger writes exist in researcher/decision/_run paths with correct Decimal handling; BUT dashboard cannot read this data due to COST-02 bug |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| No TBD/FIXME/XXX debt markers found in any Phase-4 modified files | — | — | — | — |

---

## Gaps Summary

**One gap blocking SC-5 (COST-02):** The spend dashboard (`routes.py` `spend_get`) parses `payload.get("cost_usd", "0")` at the top level of the canonical event JSON. However, `append_event` (audit/log.py) stores the full canonical subset as `payload_json`: `{"event_type":"llm_cost","payload":{...actual cost fields...},"ts":"...","user_id":"..."}`. The `cost_usd` and `strategy_name` live inside the nested `"payload"` dict.

`cost_ceiling.py` (the ceiling enforcement guard) already handles this correctly with `inner = payload.get("payload", payload)` (lines 202-204). The fix is identical and straightforward: add the same two-line unwrap in both loops in `spend_get`.

Unit tests (test_spend_route.py 6/6 GREEN) do not catch this because `_make_llm_cost_row` creates mocks with a **flat** JSON payload that bypasses the canonical wrapper. The ceiling guard passes its tests (test_cost_ceiling.py 8/8) because it tests the ceiling logic independently and uses the correct unwrap. The data-flow divergence between the two consumers of `llm_cost` events was not caught by any test.

**Impact scope:** SC-5 partly fails. The cost ledger writes ARE correct (4/4 test_cost_ledger GREEN, Decimal math correct, normalize_decimals applied). The ceiling guard reads data correctly. Only the dashboard Spend view is affected — it will display $0.00 for all spend values while the real data exists in the database.

**All other SC goals are achieved:**
- SC-1: Researcher/Decision isolation fully intact; AST gate GREEN.
- SC-2: suspicious_content event wired; _INJECTION_PATTERNS compiled; injected_content_flags on ResearchBrief; neutralization (D-40 + OrderGuard) unchanged.
- SC-3: BudgetTracker bounds unchanged; no_action first-class; degraded context trim implemented.
- SC-4: Two-tier ceiling gate deterministic and pre-LLM; Haiku triage gate; single-DM guards; cadence x2 in jobs.py; D-05 AST gate GREEN.

---

## Human Verification Required

### 1. Live cost accrual and spend dashboard

**Test:** Run multiple agent cycles (`/gekko run <strategy>`) against real Claude API; then navigate `/spend` in a browser after the nested-payload fix is applied.
**Expected:** Today's spend total matches the sum of `cost_usd` in `llm_cost` events in the audit log; per-strategy breakdown shows actual strategy names and real dollar amounts; 7-day history shows spend for days where cycles ran.
**Why human:** Requires live Claude API, running ASGI stack, and browser to confirm visual rendering with real data. Cannot be confirmed until the COST-02 gap is closed.

### 2. 80%/100% Slack DMs on real spend

**Test:** Set `daily_cost_ceiling_usd` to $0.05 (well below one research cycle cost) and run `/gekko run`. Observe Slack DMs.
**Expected:** Exactly one Slack DM at the 80% crossing (degrade message); exactly one more DM at 100% (halt message); subsequent skipped cycles produce no additional DMs on the same calendar day; raising the ceiling in Settings resumes the agent.
**Why human:** Requires real Slack workspace connected + real Claude API spend + operator action; the single-DM guards, Slack delivery, and quiet-hours bypass are all live behaviors.

### 3. Timezone-midnight ceiling reset

**Test:** Observe the ceiling reset at the user-configured timezone midnight (or temporarily set a future midnight using a different timezone).
**Expected:** Spend total resets to $0.00; agent that was halted due to 100% ceiling resumes normal cycle on first post-midnight trigger.
**Why human:** Wall-clock day boundary; cannot be simulated in unit tests without mocking system time.

### 4. Live DB migration 0005

**Test:** Run `alembic upgrade head` against the live production DB (currently at revision 0004).
**Expected:** Migration completes without error; `users` table gains the three cost-ceiling columns; `events` ck_event_type CheckConstraint accepts `llm_cost` and `suspicious_content`; no existing data disturbed.
**Why human:** Requires operator passphrase + manual DB backup before running; production DB state is the operator's responsibility.

---

_Verified: 2026-06-23_
_Verifier: Claude (gsd-verifier)_
