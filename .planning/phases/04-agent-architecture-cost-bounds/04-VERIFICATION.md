---
phase: 04-agent-architecture-cost-bounds
verified: 2026-06-24T00:00:00Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 4/5
  gaps_closed:
    - "SC-5 dashboard half: spend_get now unwraps canonical payload in both today_rows and history_rows loops; test_spend_route.py hardened with canonical-wrapper rows and regression gate (test_spend_get_canonical_payload_unwrap)"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Run agent cycles until real LLM cost events are written, then navigate /spend in a browser"
    expected: "Today's spend total matches the sum of llm_cost event cost_usd values; per-strategy breakdown shows actual strategy names and spend; 7-day history table has rows with non-zero spend for days with cycles"
    why_human: "Requires live Claude API calls, a running ASGI stack, and a browser to visually confirm the spend bars and tables render with real data from canonical-wrapped rows in the live DB"
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
**Verified:** 2026-06-24
**Status:** human_needed
**Re-verification:** Yes — after SC-5 gap closure (plan 04-06)

---

## Re-verification Summary

The single gap from initial verification (SC-5 canonical-payload unwrap bug in `spend_get`) is now closed. Commits `20af238` (routes.py fix) and `d11d48a` (test hardening) are confirmed in git history. SC-1 through SC-4 regression-checked as still green. Score advances from 4/5 to 5/5. Status moves from `gaps_found` to `human_needed` because the four human verification items (live cost accrual, real Slack DMs, tz-midnight reset, live DB migration) carried over from the initial verification and remain unresolvable programmatically.

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC-1: Researcher subagent has read-only tools only; Decision subagent consumes only a structured brief; no shared raw context | VERIFIED | `RESEARCHER_TOOLS` in researcher.py: 4 read-only tools only; `DECISION_TOOLS` in decision.py: 2 proposal tools only; brief parsed by Python before Decision; D-40 trust-boundary block in decision prompt; test_decision_prompt_isolation.py AST isolation gate GREEN — unchanged from initial verification |
| 2 | SC-2: Prompt-injection neutralized + suspicious_content audit event logged | VERIFIED | `_INJECTION_PATTERNS` at runtime.py:154 (re.compile, 5 patterns, IGNORECASE); scan between researcher and decision; `append_event(..., event_type="suspicious_content")` at lines 828-843; `injected_content_flags` on ResearchBrief; executor.py `_BYPASS_CATEGORIES` includes `"cost_alert"` — unchanged from initial verification |
| 3 | SC-3: Research turns bounded; no_action as first-class output | VERIFIED | `BudgetTracker(soft_max_calls=6 if _degradation_mode else 12)` at runtime.py:789; `_RESEARCHER_MAX_TURNS = 12`; `propose_no_action` is one of two Decision tools — unchanged from initial verification |
| 4 | SC-4: 80% degrade + Slack DM; 100% hard-halt + Slack DM; tz-midnight reset; single-DM guards; Haiku triage gate | VERIFIED | `check_cost_ceiling()` wired at runtime.py:628 (confirmed present); `just_crossed_80/just_crossed_100` guards; Haiku confined to triage gate; D-05 AST gate GREEN — unchanged from initial verification |
| 5 | SC-5: Every LLM call logged to cost ledger; dashboard shows spend per strategy + per user with ceiling visible | VERIFIED | **Gap now closed.** Ledger writes correct (unchanged). `spend_get` in routes.py now applies `inner = payload.get("payload", payload)` at line 1306 (today_rows loop) and line 1342 (history_rows loop), then reads `inner.get("cost_usd", "0")` and `inner.get("strategy_name", "Unknown")` — no remaining `payload.get("cost_usd")` or `payload.get("strategy_name")` calls anywhere in spend_get. `_make_llm_cost_row` in test_spend_route.py now emits the real canonical wrapper shape. `test_spend_get_canonical_payload_unwrap` regression gate asserts non-zero today_total ($0.08) and real strategy names ("strat-a", "strat-b") and would fail against the pre-fix top-level-read path. test_spend_route.py 7/7 GREEN. |

**Score:** 5/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/gekko/agent/cost_ceiling.py` | CeilingCheck + check_cost_ceiling() | VERIFIED | Unchanged from initial verification |
| `src/gekko/agent/pricing.py` | Pricing constants + tokens_to_usd() | VERIFIED | Unchanged |
| `migrations/versions/0005_p4_cost_ceiling.py` | Alembic 0005 migration | VERIFIED | Unchanged |
| `src/gekko/db/models.py` | User ORM + _EVENT_TYPES extended | VERIFIED | Unchanged |
| `src/gekko/agent/runtime.py` | Ceiling gate + SC-2 scan + ledger writes | VERIFIED | check_cost_ceiling at line 628 and _INJECTION_PATTERNS at line 154 confirmed present on regression check |
| `src/gekko/execution/executor.py` | cost_alert in _BYPASS_CATEGORIES | VERIFIED | Unchanged |
| `src/gekko/scheduler/jobs.py` | reschedule_strategy_degraded() + restore_strategy_normal_cadence() | VERIFIED | Unchanged |
| `src/gekko/dashboard/routes.py` | spend_get with canonical-payload unwrap in both loops | VERIFIED | Line 1306: `inner = payload.get("payload", payload)` in today_rows loop. Line 1342: `inner = payload.get("payload", payload)` in history_rows loop. Zero remaining `payload.get("cost_usd")` or `payload.get("strategy_name")` calls (grep confirmed no matches). |
| `tests/unit/test_spend_route.py` | Hardened test with canonical-wrapper rows and regression gate | VERIFIED | `_make_llm_cost_row` emits full canonical wrapper with cost fields nested inside `"payload"` key. `test_spend_get_canonical_payload_unwrap` added at line 342; asserts `"0.08"` in resp.text, `"strat-a"` in resp.text, `"strat-b"` in resp.text. 7 tests total (6 existing + 1 new). |
| `src/gekko/dashboard/templates/spend.html.j2` | Spend view template | VERIFIED | Unchanged |
| `src/gekko/dashboard/templates/settings.html.j2` | Daily ceiling fieldset | VERIFIED | Unchanged |
| `src/gekko/dashboard/templates/base.html.j2` | Spend nav link | VERIFIED | Unchanged |
| `src/gekko/schemas/research.py` | injected_content_flags field | VERIFIED | Unchanged |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| runtime.py | cost_ceiling.py | `await check_cost_ceiling(...)` | WIRED | Lines 66 (import) + 628 (call) — regression-checked |
| runtime.py | audit/log.py | `append_event(..., event_type="llm_cost")` | WIRED | Unchanged from initial verification |
| runtime.py | audit/log.py | `append_event(..., event_type="suspicious_content")` | WIRED | Unchanged |
| cost_ceiling.py | db/models.py Event | `Event.event_type == "llm_cost"` | WIRED | Unchanged |
| routes.py spend_get | payload["payload"]["cost_usd"] | `inner = payload.get("payload", payload)` then `inner.get("cost_usd", "0")` | WIRED | **Fixed by 04-06.** Both loops now unwrap canonical wrapper before reading cost fields. |
| routes.py | pricing.py | `DEFAULT_DAILY_CEILING_USD` | WIRED | Unchanged |
| migrations/0005 | models.py | frozen vocabulary copy | WIRED | Unchanged |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| spend.html.j2 today_total | `today_total` Decimal | routes.py spend_get → Event rows → `inner = payload.get("payload", payload)` → `inner.get("cost_usd", "0")` | YES — unwrap now mirrors cost_ceiling.py:202 exactly; test_spend_route.py 7/7 confirms $0.08 total from two $0.05/$0.03 canonical rows | FLOWING |
| spend.html.j2 by_strategy | `by_strategy` list | routes.py spend_get → `inner.get("strategy_name", "Unknown")` | YES — strategy names surface from canonical wrapper; "strat-a" / "strat-b" confirmed in test assertions | FLOWING |
| spend.html.j2 history | `history` 7-day buckets | routes.py spend_get → history_rows → `inner.get("cost_usd", "0")` | YES — same unwrap applied at line 1342; history loop now reads from the nested payload dict | FLOWING |
| spend.html.j2 ceiling | `ceiling` Decimal | routes.py spend_get → User.daily_cost_ceiling_usd | YES — loaded directly from User column, no payload parsing | FLOWING |
| cost_ceiling.py CeilingCheck | `current_spend` Decimal | check_cost_ceiling → Event rows → `inner = payload.get("payload", payload)` | YES — unchanged from initial; was already FLOWING | FLOWING |

---

## Behavioral Spot-Checks

Step 7b: SKIPPED — no runnable entry points without a live SQLCipher DB + Claude API credentials. The ceiling gate and spend route are exercised by unit tests, not standalone CLI commands.

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| COST-01 | 04-02, 04-03, 04-04 | Per-user pooled daily ceiling; configurable; tz-midnight reset; 80%/100% tiers; halt before LLM | SATISFIED | check_cost_ceiling() deterministic gate wired at runtime.py:628; test_cost_ceiling 8/8 GREEN |
| COST-02 | 04-05, 04-06 | Dashboard spend view: today total vs ceiling, per-strategy breakdown, 7-day history | SATISFIED | GET /spend route auth-gated; canonical-payload unwrap applied in both loops; test_spend_route.py 7/7 GREEN including regression gate |
| COST-03 | 04-05 | Configurable ceiling in Settings; Decimal validation; saves to users row | SATISFIED | Unchanged from initial verification |
| COST-04 | 04-03, 04-04 | 80% degrade; 100% halt; one DM each; D-05 Decision never Haiku | SATISFIED | Unchanged from initial verification |
| COST-05 | 04-04, 04-06 | Every LLM call logged: input_tokens, output_tokens, USD Decimal | SATISFIED | Ledger writes correct and unchanged; dashboard read path now also correct after 04-06 fix |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| No TBD/FIXME/XXX debt markers found in routes.py or test_spend_route.py (the two files modified by 04-06) | — | — | — | — |

---

## Human Verification Required

### 1. Live cost accrual and spend dashboard

**Test:** Run multiple agent cycles against real Claude API; then navigate `/spend` in a browser.
**Expected:** Today's spend total matches the sum of `cost_usd` in `llm_cost` events in the audit log; per-strategy breakdown shows actual strategy names and real dollar amounts; 7-day history shows spend for days where cycles ran.
**Why human:** Requires live Claude API, running ASGI stack, and browser to confirm visual rendering with real data from canonical-wrapped rows in the live DB.

### 2. 80%/100% Slack DMs on real spend

**Test:** Set `daily_cost_ceiling_usd` to $0.05 (below one research cycle cost) and run a cycle. Observe Slack DMs.
**Expected:** Exactly one Slack DM at the 80% crossing (degrade message); exactly one more DM at 100% (halt message); subsequent skipped cycles produce no additional DMs on the same calendar day; raising the ceiling in Settings resumes the agent.
**Why human:** Requires real Slack workspace connected + real Claude API spend + operator action; single-DM guards, Slack delivery, and quiet-hours bypass are all live behaviors.

### 3. Timezone-midnight ceiling reset

**Test:** Observe the ceiling reset at the user-configured timezone midnight (or temporarily set a future midnight using a different timezone).
**Expected:** Spend total resets to $0.00; agent that was halted due to 100% ceiling resumes normal cycle on first post-midnight trigger.
**Why human:** Wall-clock day boundary; cannot be simulated in unit tests without mocking system time.

### 4. Live DB migration 0005

**Test:** Run `alembic upgrade head` against the live production DB (currently at revision 0004).
**Expected:** Migration completes without error; `users` table gains the three cost-ceiling columns; `events` ck_event_type CheckConstraint accepts `llm_cost` and `suspicious_content`; no existing data disturbed.
**Why human:** Requires operator passphrase + manual DB backup before running; production DB state is the operator's responsibility.

---

_Verified: 2026-06-24_
_Verifier: Claude (gsd-verifier)_
