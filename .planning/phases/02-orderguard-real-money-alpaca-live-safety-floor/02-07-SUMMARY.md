---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
plan: 07
subsystem: walking-skeleton, integration-test, readme, audit-chain
tags: [walking-skeleton, hitl-06, dual-channel, real-money-demo, 7-event-chain, blocker-5-toctou-defense, manual-demo-deferred, phase-2-final]
status: complete-with-deferred-demos

# Dependency graph
requires:
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 01
    provides: |
      TradeProposal.account_mode field; STATE_TRANSITIONS 5 new edges (PENDING→AWAITING_2ND_CHANNEL etc); OrderGuardRejected exception.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 02
    provides: |
      OrderGuard skeleton + 6 BLOCK checks + cap_rejection branch in execute_proposal.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 03
    provides: |
      PDT + T+1 BLOCK + wash-sale FLAG + tenacity GET decoration + place_order AST gate.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 04
    provides: |
      WEB_ALLOWLIST + <untrusted_content> wrap + D-40 warning + directory-wide AST walk for RES-06.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 05
    provides: |
      Kill switch + cancel_all_open_orders + boot-time persistence DM + OrderGuard rejection Slack card.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 06
    provides: |
      Live credentials vault + AlpacaBroker _allow_live + HITL-06 dual-channel state machine + ProposalWriter account_mode stamp (BLOCKER #5 runtime half).
provides:
  - "tests/integration/test_promote_paper_to_live_end_to_end.py — 3 cassette tests covering: (1) full walking-skeleton 6-event chain (decision → proposal → approval[awaiting_2nd_channel] → approval[second_channel] → order_submitted → fill); (2) BLOCKER #5 TOCTOU defense — account_mode survives promote-then-demote cycle; (3) Wave-0 stub audit — zero remaining stubs in tests/"
  - "tests/fixtures/cassettes/alpaca_live_promote_smoke.json — cassette JSON documenting recorded Alpaca live exchange shapes (account / positions / quote / place_order / fill via TradingStream); placeholder credentials only"
  - "README.md §'Phase 2 — Walking-skeleton demo (OrderGuard + Real-Money Alpaca Live)' — operator-runnable recipe appended after the existing Phase-1 §; UI-SPEC §Copywriting Contract verbatim (6 matches grep-verified)"
  - "Rule 1 bug fix landed inside Task 1's commit: ProposalWriter now stamps account_mode on the ProposalRow COLUMN, not just payload_json — without this, Slack approve handler would read 'PAPER' server_default and HITL-06 dual-channel never fires for live strategies. The walking-skeleton's first-live assertion surfaced the bug."
affects: [phase-3-production-hitl-ux, phase-5-trust-ladder]

# Tech tracking
tech-stack:
  added: []  # no new runtime deps
  notes: |
    Cassette uses Phase-1 pattern from 01-09 — JSON-based; mocks AlpacaBroker.place_order
    + AlpacaFillStream + Slack DM transport + is_market_open; runs REAL ProposalWriter +
    audit chain + state machine + Block Kit card builders + OrderGuard 9-check chain.
    The hash-chain integrity (walk_chain() returns []) is the load-bearing assertion.
---

# Plan 02-07 Summary — Walking-skeleton + Real $1 Demo Recipe (PHASE 2 FINAL)

Wave 6 of Phase 2. Closes the phase with the cassette-based end-to-end test that
proves the OrderGuard + HITL-06 + live-credentials path holds together when
real-money credentials are loaded. The cassette mocks the broker HTTP and Slack
transports but runs every other layer at full fidelity — audit chain, state
machine, ProposalWriter account_mode stamping, OrderGuard with 9 checks, Block
Kit card construction, dashboard route handlers, FastAPI lifespan logic.

This plan also writes the operator-runnable README recipe that operationalizes
the real $1 demo (Task 3 — deferred to operator per 02-05 + 01-09 pattern).

## Commits

- `50d29b7` — `test(02-07-1): walking-skeleton cassette — promote paper→live → $1 limit → 6-event chain` (includes Rule 1 bug fix to ProposalWriter)
- `1b53ca0` — `docs(02-07-2): README §Phase 2 real-money walking-skeleton demo recipe`

## Decisions Made During Execution

- **6-event chain (not 7).** Earlier planning estimated 7 events; the cassette test resolved to 6 because the `first_live_trade_stamped` is folded into `on_fill_event` as a side-effect (StrategyMetadata.first_live_trade_confirmed_at UPSERT), not a separate audit event. The 6 events are: `decision → proposal → approval[awaiting_2nd_channel=True] → approval[second_channel=True] → order_submitted → fill`.
- **Rule 1 bug fix landed inline with Task 1.** The walking-skeleton's first assertion (`row.status == 'AWAITING_2ND_CHANNEL'`) surfaced that ProposalWriter was only stamping `account_mode` on `payload_json`, not on the `ProposalRow` column. Without the column stamp, the Slack approve handler reads the `'PAPER'` server_default and the HITL-06 dual-channel divert never fires. The fix is a one-line addition to `src/gekko/agent/proposal_writer.py` — `account_mode=tp.account_mode` in the ProposalRow construction. Committed in `50d29b7` alongside the test that surfaced it (Rule 1 of the auto-fix protocol: bug in current-plan scope, ≤3 attempts → auto-fix).
- **Second-proposal single-channel sanity** is the same test file. After the first-live fill stamps `first_live_trade_confirmed_at`, the cassette runs a SECOND proposal for the same strategy and asserts: PENDING → APPROVED (no AWAITING_2ND_CHANNEL transition), executor dispatched directly, no dashboard step required. This validates D-32's "first-live gate is per-strategy, not per-trade" contract.
- **BLOCKER #5 TOCTOU defense test is in this plan** (not in 02-06) because it requires the full strategy-promote → ProposalWriter → state-machine flow that only the walking-skeleton sets up end-to-end. The unit test in 02-06 covers the ProposalWriter stamp logic; the integration test in 02-07 covers the survives-promote-then-demote cycle (the actual TOCTOU race).
- **README recipe mirrors Phase-1's `Phase 1 — Walking-skeleton demo` shape verbatim** for operator familiarity. Order: pre-demo checks → setup → first-live trade → subsequent live trade → kill switch sanity → acceptance criteria. UI-SPEC §Copywriting Contract strings are quoted directly so operator can grep for them in the actual UI (verifies UI-SPEC and code stayed in sync).
- **Cassette format reused from 01-09** — JSON-based, not VCR/yaml. Phase-1 chose this format because it's easier to read + edit; 02-07 follows the same convention. Placeholder credentials only (no real API keys).

## Files Created / Modified

### Created
- `tests/integration/test_promote_paper_to_live_end_to_end.py` (3 cassette tests)
- `tests/fixtures/cassettes/alpaca_live_promote_smoke.json` (recorded Alpaca live exchange shapes)

### Modified
- `README.md` (appended §"Phase 2 — Walking-skeleton demo (OrderGuard + Real-Money Alpaca Live)" after the existing Phase-1 §)
- `src/gekko/agent/proposal_writer.py` (Rule 1 auto-fix — added `account_mode=tp.account_mode` to ProposalRow construction; the column stamp was missing)

## Verification

### Automated (full suite green)

- `uv run pytest tests/integration/test_promote_paper_to_live_end_to_end.py -x -q` → 3 passed
- `uv run pytest tests/integration/test_trigger_run_end_to_end.py -x -q` → 1 passed (Phase-1 walking-skeleton not regressed)
- Combined Phase-1 + Phase-2 integration batch (8 files) → 31 passed
- Phase-1 + Phase-2 relevant unit batch (18 files) → 189 passed
- AST gates: `AlpacaBroker.place_order` + `OrderGuard.place_order` zero-decorator → 9 passed
- Wave-0 stubs in tests/ → 0 remaining (test_no_wave_0_stubs_remain_in_tests_directory passes)
- No `claude_agent_sdk` import in cassette fixtures or test files
- No new vendored assets; no new utility classes; CSP `script-src 'self'` unchanged

### Manual — DEFERRED to operator (`VALIDATION.md` Manual-Only Verification #1)

Real-money $1 demo requires actual Alpaca LIVE credentials + real money on the line. Per operator's choice (same path as 02-05 + Phase-1 01-09), demo is deferred to a future session. The full demo recipe is in:

- `README.md` §"Phase 2 — Walking-skeleton demo (OrderGuard + Real-Money Alpaca Live)"
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/deferred-items.md` under "Manual demos deferred (Plan 02-07 walking-skeleton)"

**Demo summary (full recipe in README.md):**

Setup: real Alpaca LIVE account (≥$5 buying power) + Cloudflared/ngrok/Tailscale tunnel with `DASHBOARD_URL` set + Slack DM with bot.

1. `uv run gekko credentials add-alpaca-live` (paste real keys; `hide_input=True`)
2. `uv run gekko strategy promote-live ai-infra-bull` (typed-confirm by name)
3. Dashboard `mode` toggle paper → live + save
4. Slack `/gekko run ai-infra-bull` → expect dedicated `🔴 FIRST LIVE TRADE — DUAL CONFIRM REQUIRED` card (URL-button only)
5. Click `Open Dashboard to Confirm` → tick BOTH checkboxes → wait 5s countdown → click `Confirm First Live Trade`
6. Wait for fill DM `🔴 LIVE: Filled BUY 1 AAPL @ $X.XX ...`
7. Verify on Alpaca live dashboard: order shows `filled`, position appears
8. `gekko audit verify` → expect "Chain intact across N events" (N ≥ 28: Phase-1 22 + Phase-2 first-live 6+)
9. `gekko audit dump --limit 10` shows the 6-event chain
10. Second `/gekko run ai-infra-bull` → REGULAR HITL card (single-channel; first-live gate skipped after stamp)
11. Kill switch sanity: `/gekko kill` + `KILL` → cancel within 5s → banner stacking visible → `/gekko unkill` + `UNKILL` to resume

**Acceptance criteria for `demo_passed`:**
- ☐ 6-event first-live chain validates via `gekko audit verify`
- ☐ Dual-channel gate fires on first trade, skipped on second trade
- ☐ Red `[LIVE — REAL MONEY]` banner + `[LIVE]` chip + first-live Slack card + LIVE rendering on second-trade HITL card all match UI-SPEC copy verbatim
- ☐ Kill switch fires within 5s, persists across observation, can be unkilled
- ☐ No audit chain breaks
- ☐ Real $1 trade lands on Alpaca live (broker_order_id recorded)

## Decisions Carried Forward / Phase-3 Backlog

- **Executor-error → Slack DM surfacing on MarketClosed / BrokerOrderError** is still Phase-3 backlog (carry-forward from v1.0 Phase 1 Plan 01-09). Not in scope for Phase 2.
- **Production HITL UX hardening** (idempotent buttons, quiet hours, timeout=REJECT, edit-size, dashboard fallback for non-first-live trades) lives in Phase 3.
- **Two-tier cost ceiling** (80% graceful degradation + 100% hard halt) is Phase 4 — but Plan 02-03's broker rate-limit backoff is forward-compatible with the cost ledger.
- **Trust Ladder** (per-strategy propose-only → auto-within-caps + portfolio caps + anomaly auto-demote) is Phase 5 — HITL-06's first-live gate is the only graduation gate Phase 2 ships.

## Phase 2 — Final Status

Phase 2 ships:
- ✅ OrderGuard decorator (D-26) with 9 BLOCK checks (universe, hard_caps, qty_price, paper_live, kill_active, market_hours, PDT, T+1, wash_sale stamp)
- ✅ Wash-sale FLAG (EXEC-09) — surface only, never blocks
- ✅ Broker rate-limit backoff on GETs only (EXEC-08) via tenacity + AST gate proving place_order zero-decorator
- ✅ Kill switch (EXEC-06) — 5s SLA target + DB persistence + 3 surfaces (Slack two-step + CLI + dashboard typed-KILL) + boot-time DM
- ✅ Live credentials vault (BROK-A-02) — SQLCipher whole-DB encryption per Phase-1 D-19
- ✅ HITL-06 dual-channel — PENDING → AWAITING_2ND_CHANNEL → APPROVED_LIVE; first-live gate per strategy
- ✅ Red `[LIVE — REAL MONEY]` banner stacking above kill banner per UI-SPEC
- ✅ RES-06/07 prompt-injection minimums — source allowlist + `<untrusted_content>` wrap + Decision system_prompt D-40 warning + directory-wide AST walk
- ✅ BLOCKER #1 — STATE_TRANSITIONS extended with 5 new edges
- ✅ BLOCKER #3 — encryption claim honest (SQLCipher whole-DB, not Fernet)
- ✅ BLOCKER #4 — `_allow_live` grep gate (AST walk over src/gekko/ verifies `_allow_live=True` / `paper=False` appears ONLY in `_build_broker`)
- ✅ BLOCKER #5 — TradeProposal.account_mode TOCTOU closure (schema + runtime stamp + read-side rewire + TOCTOU defense test)
- ✅ BLOCKER #6 — RES-06 directory-wide AST walk (not single-module grep)
- ✅ BLOCKER #7 — executor.py declared in 02-05 files_modified

⚠ Manual demos deferred (3 from 02-05 + 1 from 02-07):
- 02-05 Demo A: kill switch 5s SLA
- 02-05 Demo B: cross-restart persistence
- 02-05 Demo C: dashboard typed-KILL modal flow
- 02-07: real $1 first-live trade end-to-end

All four require real Alpaca live + real Slack + real Chrome session + real wall-clock observation. Logged in `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/deferred-items.md`.

Phase-3 carry-forward: executor-error → Slack DM (MarketClosed / BrokerOrderError) — already Phase-1 carry-forward; remains Phase-3 backlog.

---

*Plan 02-07 closed with manual-demo deferred 2026-06-17. Phase 2 complete (with manual demos pending). Same pattern as Phase-1 Plan 01-09 (deferred 2026-06-11, demo passed 2026-06-12 with 22-event audit chain proof).*
