---
phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
plan: 05
subsystem: auto-execution
tags: [trust-ladder, auto-execute, orderguard, dual-channel, quiet-hours, slack, htmx, daily-digest, decimal, tdd, pytest]

# Dependency graph
requires:
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 01
    provides: "migration 0007 trust columns + auto_execution/anomaly_demotion event types; enriched approval payload (strategy_name + account_mode); Wave-0 RED stubs (test_auto_execute.py, test_trust_safety_invariants.py)"
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 02
    provides: "trust.load_trust_level + TRUST_AUTO (sole writer of auto-within-caps, AST-locked); chip-auto-executed CSS"
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 03
    provides: "OrderGuard place_order pipeline stacking portfolio + capital caps (the last-line re-check the auto path inherits)"
  - phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps
    plan: 04
    provides: "executor _BYPASS_CATEGORIES with anomaly_demotion (bypass tier); post-fill anomaly hook; anomaly_demotion audit event the digest reads"
  - phase: 02-orderguard-real-money-alpaca-live
    provides: "execute_proposal → OrderGuard; AWAITING_2ND_CHANNEL dual-channel gate + first_live_trade_confirmed_at stamp; _send_slack_dm_respecting_quiet_hours + _send_slack_dm_blocks"
provides:
  - "gekko.agent.runtime._run_auto_branch — deterministic auto-approve + execute_proposal dispatch for auto-within-caps strategies; LIVE+first-trade → AWAITING_2ND_CHANNEL"
  - "trigger_strategy_run auto-branch guard (trust evaluated ONCE) + run-summary auto_outcome"
  - "gekko.reporter.slack.build_auto_execution_dm — Surface-4a informational Block Kit (no actions block; rationale escaped + truncated)"
  - "executor._send_slack_dm_blocks_respecting_quiet_hours — quiet-hours-respecting Block Kit sender; on_fill_event auto-exec detection + FYI DM (ROUTINE category)"
  - "_proposal_card.html.j2 AUTO-EXECUTED blue chip + suppressed Approve/Reject for execution_path==auto"
  - "daily_pnl auto_execution + anomaly_demotion aggregation branches + digest lines (Surface 4c / 6c)"
affects: [scheduler-lifespan, dashboard-approvals, daily-digest]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "The auto-branch sits BELOW the SDK boundary (deterministic Python in trigger_strategy_run after write_proposal) — the LLM never decides trust level"
    - "Single enforcement path: the auto trade reaches the broker ONLY via execute_proposal → OrderGuard (D-T08); no second order path; grep gate proves no broker.place_order in runtime.py"
    - "Trust evaluated ONCE at proposal-build time (TOCTOU lesson); account_mode read from the LOCKED proposal row; demotion takes effect next cycle"
    - "LIVE + auto STACKS the Phase-2 first-live dual-channel gate (first_live_trade_confirmed_at IS NULL → AWAITING_2ND_CHANNEL, never direct execute)"
    - "Quiet-hours bypass-vs-respect is the category switch: auto-exec FYI is ROUTINE (respects); anomaly-demotion is BYPASS (D-T13) — inverting them is the documented anti-pattern"
    - "Auto-exec fill detected from the approval event's execution_path=auto discriminator at fill time"

key-files:
  created: []
  modified:
    - src/gekko/agent/runtime.py
    - src/gekko/execution/executor.py
    - src/gekko/reporter/slack.py
    - src/gekko/dashboard/templates/_proposal_card.html.j2
    - src/gekko/reporter/daily_pnl.py
    - tests/unit/test_auto_execute.py
    - tests/unit/test_trust_safety_invariants.py
    - tests/unit/test_executor.py
    - tests/unit/test_daily_pnl_aggregation.py

key-decisions:
  - "The LIVE first-trade stamp is read through the run's injected session_factory (s.get(StrategyMetadata, (user_id, name))) rather than promotion.load_strategy_metadata, so the auto-branch is TOCTOU-consistent with the run AND testable via the injected seam (load_strategy_metadata would open its own per-user engine + passphrase). Same (user_id, strategy_name) lookup + first_live_trade_confirmed_at check as the Slack approve handler."
  - "proposals.py left functionally UNCHANGED. The plan listed it in files_modified for 'approve_proposal accepts the auto actor + execution_path=auto in extra_payload', but approve_proposal already accepts an arbitrary actor and merges extra_payload (Plan 01), and the enriched approval already carries strategy_name + account_mode. The auto-branch passes actor='auto-execute' + extra_payload={execution_path:'auto'} with no signature change required — the additive contract was already in place."
  - "Auto-exec fill detection reads the approval event (execution_path=auto) inside on_fill_event rather than adding a column to the proposals row. One approval event per proposal makes the targeted scan cheap and avoids a migration; the discriminator is already written by the auto-branch."
  - "_run_auto_branch returns the outcome string (auto_executed / awaiting_2nd_channel) which trigger_strategy_run surfaces as run-summary auto_outcome; the original tool_outcome (trade/no_action) is preserved unchanged so propose-only surfaces still render the HITL card."
  - "Digest auto-exec + anomaly lines are strictly ADDITIVE — only appended when the count/list is non-empty, so the existing block indices (gross=[1], per-strategy=[2]) and the four prior digest tests are untouched."

patterns-established:
  - "Auto-execution surfacing is one informational contract across three surfaces (FYI DM, card chip, digest) — none carry approve/reject controls; all read the auto_execution audit event / execution_path discriminator"
  - "Block Kit quiet-hours-respecting sender mirrors the text variant's bypass-vs-respect routing through the same _BYPASS_CATEGORIES set"

requirements-completed: [TRUST-02, TRUST-06]

coverage:
  - id: T1
    description: "auto-within-caps TradeProposal → approve_proposal(actor=auto-execute, execution_path=auto) + auto_execution event → execute_proposal → OrderGuard (single path); NoActionProposal + propose-only do not; LIVE+first-trade → AWAITING_2ND_CHANNEL (no direct execute); cap breach on auto path → cap_rejection + FAILED; trust evaluated once; no broker.place_order in runtime.py"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_auto_execute.py (11 tests: routing, auto_execution event payload, live-first dual-channel, live-confirmed executes, propose-only/no-action no-op, OrderGuard re-check cap_rejection+FAILED, grep gate, card chip) + test_trust_safety_invariants.py (AST guards: auto literal + no broker path + first_live/AWAITING_2ND_CHANNEL stack)"
        status: pass
    human_judgment: false
  - id: T2
    description: "auto-exec informational DM (no actions block) via ROUTINE category that respects quiet hours; DM failure never aborts the fill; AUTO-EXECUTED card chip + suppressed Approve/Reject; anomaly(bypass) vs auto(respect) not inverted"
    requirement: "TRUST-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_executor.py (auto-exec DM category respects quiet hours, no-actions-block, failure-swallow, HITL fill sends no FYI, anti-pattern source guard, build_auto_execution_dm escape/truncate) + test_auto_execute.py (card chip render + no actions; HITL card unchanged)"
        status: pass
    human_judgment: false
  - id: T3
    description: "digest counts auto_execution (aggregate + per-strategy 🤖) and renders anomaly-demotion summary line; existing digest tests still pass (additive)"
    requirement: "TRUST-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_daily_pnl_aggregation.py (7 tests: 4 existing + auto-exec aggregation/annotation, anomaly summary line, additive omission)"
        status: pass
    human_judgment: false

# Metrics
duration: 35min
completed: 2026-06-26
status: complete
---

# Phase 5 Plan 05: Auto-Execution Slice Summary

**Ships the slice that actually hands execution authority to the agent (TRUST-02 / SC-2 + TRUST-06): a deterministic auto-branch in `trigger_strategy_run` that, for an `auto-within-caps` strategy, auto-approves a TradeProposal and executes it WITHOUT HITL through the existing `execute_proposal` → OrderGuard last line (so portfolio + capital + hard caps are re-checked), writes a full-rationale `auto_execution` audit event, and stays legible via three informational surfaces — a quiet-hours-respecting FYI Slack DM, an `AUTO-EXECUTED` card chip, and daily-digest lines. Critically, a LIVE auto strategy still passes the Phase-2 first-live dual-channel gate: an unconfirmed first live trade routes to `AWAITING_2ND_CHANNEL`, never direct execute. The auto path is structurally incapable of bypassing a guard — there is exactly one order path.**

## Performance
- **Duration:** ~35 min
- **Completed:** 2026-06-26
- **Tasks:** 3 (all TDD: RED contract → GREEN)
- **Files:** 9 modified across 3 task commits

## Accomplishments
- **Auto-branch (`runtime.py` `_run_auto_branch`, TRUST-02 / SC-2):** Inserted after the `write_proposal` block in `trigger_strategy_run` — deterministic Python BELOW the SDK boundary. Trust is evaluated ONCE here via `load_trust_level(account_mode=proposal.account_mode)`; for an `auto-within-caps` TradeProposal it calls `approve_proposal(actor="auto-execute", extra_payload={execution_path:"auto"})`, writes the `auto_execution` event (proposal_id, strategy_name, account_mode, side, qty, ticker, rationale_summary), then dispatches `execute_proposal` → OrderGuard (the single enforcement path; D-T08). The run summary gains `auto_outcome` (`auto_executed` / `awaiting_2nd_channel`). `NoActionProposal` and `propose-only` never enter the branch.
- **Live+auto gate stacking (D-T03 / TRUST-06):** A LIVE proposal whose strategy's `first_live_trade_confirmed_at IS NULL` is routed to `AWAITING_2ND_CHANNEL` (HITL dual-channel) exactly as the Slack approve handler does — transition + approval event with `awaiting_2nd_channel:True`, NO direct execute. `account_mode` is read from the locked proposal row (TOCTOU-safe). live + auto inherits every guard.
- **OrderGuard re-check proven (D-T08):** A behavioral test runs the REAL `execute_proposal` with a broker whose `place_order` raises `OrderGuardRejected` — the auto path lands `cap_rejection` + FAILED, proving caps are re-checked as the last line. `grep -n 'broker.place_order' src/gekko/agent/runtime.py` is empty (no second order path).
- **Auto-execution FYI DM (`executor.py` + `reporter/slack.py`, D-T18):** `build_auto_execution_dm` renders the Surface-4a informational Block Kit (headline + rationale ~140-char escaped + "No action needed — FYI" context; NO actions block). `on_fill_event` detects an auto-executed fill via the approval event's `execution_path=auto` discriminator and sends the FYI via the new `_send_slack_dm_blocks_respecting_quiet_hours` with the ROUTINE `auto_execution` category — SUPPRESSED in quiet hours (NOT the bypassing `anomaly_demotion` tier; inverting them is the documented anti-pattern). DM-send exceptions are swallowed so they never abort the fill.
- **Card chip (`_proposal_card.html.j2`, Surface 4b):** Auto-executed proposals render the blue `.chip-auto-executed` (`AUTO-EXECUTED`, aria-label) in the header and suppress the Approve/Reject actions row (the timeline is already closed). Uses the chip CSS shipped in Plan 02.
- **Daily digest (`daily_pnl.py`, Surface 4c / 6c):** `_aggregate_today_events` gains `auto_execution` (count + per-strategy attribution) and `anomaly_demotion` (latest-wins per strategy) branches; `_build_digest_blocks` renders `🤖 Auto-executed today: {N} trades across {M} strategies`, appends ` 🤖` to per-strategy lines for auto strategies, and renders `🛑 Anomaly demotions today: {name} (−{X}% single-day)`. All money/percent math is Decimal; the new lines are additive (only rendered when present, prior block indices preserved).

## Task Commits
1. **Task 1: auto-branch + live+auto gate + auto_execution event** — `548c487` (feat)
2. **Task 2: auto-exec informational DM + AUTO-EXECUTED card chip** — `a2439fb` (feat)
3. **Task 3: daily digest auto_execution + anomaly_demotion surfacing** — `ff6f5e7` (feat)

## Deviations from Plan
None requiring auto-fix rules. Two scope decisions (documented in `key-decisions`):

- **`proposals.py` left functionally unchanged.** The plan listed it in `files_modified` for "approve_proposal accepts the auto actor + execution_path in extra_payload (additive)", but that contract already existed from Plan 01 (`approve_proposal` accepts any `actor` and merges `extra_payload`; the enriched approval already carries `strategy_name` + `account_mode`). The auto-branch uses the existing additive signature with no change — so the named outcome was delivered without editing the file.
- **First-live stamp read via the injected `session_factory`** (not `promotion.load_strategy_metadata`) so the auto-branch is TOCTOU-consistent with the run and testable through the injected seam.

## Decisions Made
See `key-decisions` frontmatter: injected-session-factory metadata read, proposals.py-unchanged, approval-event auto detection (no migration), `auto_outcome` run-summary field, additive digest lines.

## Known Stubs
None. The auto-branch, FYI DM, card chip, and digest lines are all wired to live data (`load_trust_level` + `StrategyMetadata` + the `auto_execution` / `anomaly_demotion` audit events). The scheduler lifespan still needs to arm the Plan-04 anomaly jobs in production (noted in 05-04 Next Phase Readiness); that is a serve-path wiring concern outside this plan's auto-execution slice.

## Threat Flags
None beyond the plan's `<threat_model>` (T-05-20..25, all mitigated):
- **T-05-20 (auto bypassing promotion):** auto-branch guarded by `trust == TRUST_AUTO`; `trust.py` is the sole writer (AST gate intact).
- **T-05-21 (auto reaching broker without OrderGuard):** routed through `execute_proposal`; grep gate empty; cap-breach behavioral test → `cap_rejection` + FAILED.
- **T-05-22 (LIVE+auto skipping dual-channel):** `first_live_trade_confirmed_at` check → `AWAITING_2ND_CHANNEL`; behavioral test locks it.
- **T-05-23 (auto not auditable):** `auto_execution` event with full rationale; digest + card surface it.
- **T-05-24 (TOCTOU):** trust evaluated once; `account_mode` from the locked row.
- **T-05-25 (FYI DM ignoring quiet hours):** ROUTINE category, suppressed in quiet hours; anti-pattern source guard test.

No new unescaped HTML interpolation introduced — the card chip is a static deterministic string; `build_auto_execution_dm` escapes the LLM-authored rationale via `_escape_mrkdwn`.

## Issues Encountered
- My own explanatory comment text "broker.place_order is never called here" tripped the D-T08 grep gate on first run; reworded to "the broker is never called directly". The literal substring now appears nowhere in `runtime.py`.
- `executor.py` needed `import json` + `from gekko.db.models import Event` for the approval-event auto detection (added; SDK-free invariant intact — `grep -c claude_agent_sdk` = 0).
- Test env: ran via `.venv/Scripts/python.exe -m pytest` per MEMORY; scoped to the plan's named files + related regressions (full suite hangs at exit, exit 124 ≠ failure).

## Verification
- `tests/unit/test_auto_execute.py test_trust_safety_invariants.py test_executor.py test_daily_pnl_aggregation.py` → **42 passed**.
- `grep -n 'broker.place_order' src/gekko/agent/runtime.py` → **empty**.
- Related regression (cost_ceiling, suspicious_content, cost_ledger, trust_streak, trust_routes, anomaly, orderguard, proposal_card_shared_partial, quiet_hours_dm_gate, slack_block_kit) → **100 passed**.
- `grep -c claude_agent_sdk src/gekko/execution/executor.py` → **0** (executor SDK-free invariant intact).

## Next Phase Readiness
- This is the final plan in Phase 5. The trust ladder is complete: promote/demote surface (02), portfolio + capital caps in OrderGuard (03), anomaly auto-demotion reflex (04), and now the auto-execution slice (05) that hands execution authority within the stacked caps and the live+auto dual gate.
- Production serve path should arm the Plan-04 anomaly scheduler jobs (`register_anomaly_evaluator` + `register_market_open_snapshot`) in the FastAPI lifespan alongside `register_daily_pnl_cron`.

## Self-Check: PASSED

---
*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Completed: 2026-06-26*
