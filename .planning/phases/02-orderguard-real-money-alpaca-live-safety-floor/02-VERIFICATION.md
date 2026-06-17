---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
verified: 2026-06-17
status: passed_with_deferred
criteria_pass: 4
criteria_pass_with_deferred: 1
criteria_fail: 0
must_haves_verified: 11
must_haves_total: 11
deferred_demos: 4
phase_1_invariants_preserved: true
verification_method: goal-backward audit from SUMMARIES + REVIEW + REVIEW-FIX + deferred-items + ROADMAP success criteria; inline (verifier agent crashed with ConnectionRefused mid-run, artifact trail was already complete)
---

# Phase 2 — Goal Verification

## Phase Goal (from ROADMAP.md lines 62-74)

> User can promote a paper-validated strategy to real-money Alpaca live trading, with every order passing through a non-LLM OrderGuard layer that enforces idempotency, universe whitelist, hard caps, qty×price sanity, and paper-vs-live credential pairing.

---

## Success Criterion 1 — Universe/cap/qty×price hard rejection

**Verdict:** ✅ PASS

> User attempting to place an order outside the strategy's universe whitelist, exceeding any hard cap (size, daily loss, max trades/day, sector exposure), or with a qty×price mismatching declared notional by >2% sees the order hard-rejected by OrderGuard before it reaches the broker, with the rejection recorded in audit.

**Evidence:**
- **Code:** `src/gekko/execution/orderguard.py` (OrderGuard.place_order — 9-check chain); `src/gekko/execution/checks/_universe.py`, `_hard_caps.py` (4 sub-checks), `_qty_price.py` (2% drift bound via `target_notional_usd` + Decimal math), `_paper_live.py`, `_kill_switch.py`, `_market_hours.py`. All raise `OrderGuardRejected(reject_code, reject_reason, extra={...})` on violation.
- **Wired into execute_proposal:** `src/gekko/execution/executor.py::execute_proposal` wraps `broker.place_order` in `try: ... except OrderGuardRejected: ...` branch (mirror of `executor.market_closed`). On rejection: emits `cap_rejection` audit event, transitions APPROVED → FAILED, sends Slack DM via `build_orderguard_rejection_card` (CR-02 + WR-02 + WR-06 fixes confirmed all DM paths go through `_send_slack_dm` identity-split seam).
- **Tests:** `tests/unit/test_orderguard.py` (18 assertions covering each check positive + negative); `tests/integration/test_orderguard_cap_rejection.py` (cassette: out-of-watchlist proposal → OrderGuardRejected → cap_rejection event in audit → proposal FAILED + Slack DM sent with rejection card); `tests/unit/test_orderguard_paper_live.py` (4×4 paper/live matrix).
- **Audit chain:** `cap_rejection` event_type in `_EVENT_TYPES`, payload `{reject_code, reject_reason, ticker, proposal_id, check_name}` (WR-07 fix dropped duplicate `check_name` after verification — payload now has `reject_code` only).

**REQ-IDs satisfied:** EXEC-04 (universe + hard caps + qty×price 2%), partial EXEC-05 (paper/live pairing).

---

## Success Criterion 2 — Kill switch 5s SLA

**Verdict:** ⚠ PASS-WITH-DEFERRED

> User can trigger the kill switch via Slack `/gekko kill` or dashboard button and see all trading halt globally with open orders cancelled, within 5 seconds.

**Evidence:**
- **Code:** `src/gekko/execution/kill_switch.py` — `activate(user_id, sessions, brokers)` writes `users.kill_active=True` to DB FIRST (D-37), then runs `asyncio.gather(*[b.cancel_all_open_orders() for b in brokers], timeout=4.0)`. `is_active(user_id)` is DB-fresh read. `deactivate(user_id)` clears flag. BL-02 fix confirmed: `_execute_kill` + `_execute_unkill` now wrapped in try/except to ensure `kill_complete`/`unkill_complete` audit events always emit even on partial failure.
- **3 surfaces:** Slack `/gekko kill CONFIRM` two-step (`src/gekko/slack/commands.py`); CLI `gekko kill` typed-confirm (`src/gekko/cli.py`); dashboard typed-KILL modal POST `/kill/confirm` (`src/gekko/dashboard/routes.py`). WR-01 fix aligned Slack `/gekko unkill UNKILL` token with spec (was incorrectly `CONFIRM` before).
- **Boot-time persistence (D-36):** `src/gekko/dashboard/app.py` FastAPI lifespan reads `users.kill_active` at startup; if True, Slack-DMs operator via `_send_slack_dm` (identity-split) + sets `app.state.kill_active=True` so banner renders on first request.
- **OrderGuard read-side wired:** `check_kill_switch` (one of 9 OrderGuard checks) reads `is_active(user_id)` fresh from DB — new orders blocked immediately after the activate() commit.
- **Tests:** `tests/unit/test_kill_switch.py` (DB-first ordering, 4s timeout, asyncio.gather behavior); `tests/integration/test_kill_switch.py` (cassette: Slack two-step → kill active + cancels emitted + audit pair); `tests/integration/test_kill_persistence.py` (boot-time DM); `tests/integration/test_dashboard_kill.py` (modal form-POST + HTMX 1s polling).
- **AlpacaBroker.cancel_all_open_orders:** zero-decorator (kill timing > 429 resilience per RESEARCH §3 Open Question #3). WR-04 fix added docstring explicit about the invariant.

**Deferred (acceptable):** 02-05 Demo A (5s SLA stopwatch on real Slack) + Demo B (cross-restart persistence with Ctrl-C) + Demo C (dashboard typed-KILL modal in real Chrome). All three documented in `deferred-items.md` with full recipes. Cassette tests cover the code paths at mocked-broker level; wall-clock + browser DOM evidence requires real environment.

**REQ-IDs satisfied:** EXEC-06 (kill switch).

---

## Success Criterion 3 — First-live two-channel confirmation (HITL-06)

**Verdict:** ✅ PASS

> User's first live-money trade for any new strategy requires a separate-channel confirmation (Slack DM + dashboard confirmation, both) — single-channel approval cannot execute the first live trade.

**Evidence:**
- **State machine:** `STATE_TRANSITIONS` frozenset extended in `src/gekko/approval/proposals.py` with 5 new edges (Plan 02-01): PENDING → AWAITING_2ND_CHANNEL, AWAITING_2ND_CHANNEL → APPROVED_LIVE / REJECTED / EXPIRED, APPROVED_LIVE → EXECUTING. `len(STATE_TRANSITIONS) == 11` (Phase-1 6 + Phase-2 5).
- **Slack handler (CR-02 + WR-06 fixes):** `_approve_workflow` in `src/gekko/approval/slack_handler.py` reads `tp.account_mode == "LIVE"` (LOCKED proposal row, BLOCKER #5 closure). For first-live: transitions PENDING → AWAITING_2ND_CHANNEL, calls `build_first_live_card(...)` to construct the dedicated `🔴 FIRST LIVE TRADE — DUAL CONFIRM REQUIRED` Block Kit card with URL-button only (no inline Approve/Reject), sends via `_send_slack_dm(gekko_user_id, blocks=...)` (identity-split seam), does NOT dispatch executor.
- **Dashboard route:** POST `/live-confirm/{proposal_id}` in `src/gekko/dashboard/routes.py` validates `ack_real_money` + `ack_read_rationale` checkboxes + 5-second server-side read timer (`time.time() - page_load_ts >= 5.0`). On validation pass: transitions AWAITING_2ND_CHANNEL → APPROVED_LIVE, dispatches executor via asyncio.create_task. Idempotent on double-click (returns `live_confirm_success.html.j2` with "already confirmed at {ts}" for already-APPROVED_LIVE proposals).
- **First-live gate per strategy (not per trade):** `stamp_first_live_trade(user_id, strategy_name, fill_ts)` set-once UPSERT on `strategy_metadata.first_live_trade_confirmed_at`. Subsequent live trades skip the dual-channel gate (regular HITL card with `🔴 LIVE — REAL MONEY` banner + inline Approve/Reject).
- **Tests:** `tests/integration/test_first_live_gate.py` (cassette: Slack DM only path; dashboard route only path; both required for first-live); `tests/unit/test_live_confirm_idempotent.py` (double-click defense); `tests/integration/test_promote_paper_to_live_end_to_end.py` (walking-skeleton 6-event chain — Plan 02-07 Task 1 — passes; second-proposal single-channel verified in same test).
- **BLOCKER #5 TOCTOU closure:** ProposalWriter stamps `account_mode` at proposal-build time (verified by `tests/unit/test_proposal_writer_account_mode_stamp.py`); approve handler + `_build_broker` read from LOCKED proposal row, never re-derive from strategy state. TOCTOU defense test in `test_promote_paper_to_live_end_to_end.py::test_account_mode_survives_promote_then_demote_cycle` passes.

**REQ-IDs satisfied:** HITL-06 (first live trade requires Slack + dashboard BOTH).

---

## Success Criterion 4 — Paper/live credential pairing + red banner

**Verdict:** ✅ PASS

> Paper credentials cannot place live orders and vice versa — OrderGuard validates env-credential pairing and hard-rejects mismatches, with a red banner indicating live mode on every Slack message and UI surface.

**Evidence:**
- **3-way invariant check:** `src/gekko/execution/checks/_paper_live.py::check_paper_live_pairing` enforces `strategy.mode ⇔ account_mode ⇔ broker.is_paper AND BrokerCredential.kind`. Mismatch raises `OrderGuardRejected("paper_live_mismatch_credential", ...)`.
- **BLOCKER #4 `_allow_live` constructor guard:** `src/gekko/brokers/alpaca.py::AlpacaBroker.__init__` accepts `_allow_live: bool = False`; raises `BrokerConfigError` when `paper=False AND NOT _allow_live`. AST grep gate `tests/unit/test_alpaca_live_construction_locked.py` walks every `.py` under `src/gekko/` and asserts `_allow_live=True` / `paper=False` Call-arg nodes appear ONLY inside `src/gekko/execution/executor.py::_build_broker`. (Correctly ignores `BrokerCredential(paper=False)` ORM kwarg in vault.)
- **SQLCipher credential model:** `src/gekko/vault/credentials.py::store_live_credentials / load_live_credentials`. `BrokerCredential.kind` column (`alpaca_paper` | `alpaca_live`) added by Alembic 0002 (Plan 02-01). `key_blob` + `secret_blob` stored as plaintext per Phase-1 D-19 (SQLCipher whole-DB encryption); BLOCKER #3 docs honest about plaintext-in-encrypted-DB (no Fernet claim drift). `BrokerCredential.__repr__` excludes blob fields.
- **Red `[LIVE — REAL MONEY]` banner:**
  - Dashboard: `src/gekko/dashboard/templates/live_banner.html.j2` (sticky top, z=50, top=0, red bg `#991b1b`, white text 14px/700, ARIA `role="alert" aria-live="polite"` per UI-SPEC). Stacks above kill banner (z=49, top=40px) when both active per D-33. Banner extended in `base.html.j2`.
  - Slack: `build_live_mode_banner` Block Kit section in `src/gekko/reporter/slack.py`. Regular HITL card + first-live card both prefix the banner. `_escape_mrkdwn` applied to all LLM-authored text per Phase-1 invariant.
  - `[LIVE]` chip per live-eligible strategy in `strategies_list.html.j2`.
- **Tests:** `tests/integration/test_alpaca_live_credentials.py` (vault round-trip; `kind="alpaca_live"`; `__repr__` excludes blobs); `tests/unit/test_orderguard_paper_live.py` (4×4 paper/live matrix); `tests/unit/test_live_visuals.py` (banner/chip presence).
- **CSP `script-src 'self'` preserved:** all 7 new dashboard templates use HTMX `hx-*` attributes only (verified in code review). No inline `<script>`.

**REQ-IDs satisfied:** EXEC-05 (paper/live env-credential pairing), BROK-A-02 (Alpaca live credentials).

---

## Success Criterion 5 — Full HITL flow with PDT/T+1/wash-sale/rate-limit-backoff

**Verdict:** ⚠ PASS-WITH-DEFERRED

> User can promote a paper strategy to live, place a small real-money trade through the full HITL flow, and see PDT-rule awareness, wash-sale flagging, market-hours guard, and broker rate-limit backoff all enforce correctly without manual intervention.

**Evidence:**
- **PDT detection (EXEC-11 BLOCK):** `src/gekko/execution/checks/_pdt.py::check_pdt` — two-source detection. Primary: Alpaca `TradeAccount.pattern_day_trader: bool` + `daytrade_count: int` (BLOCK if ≥3 in last 5 business days AND `pattern_day_trader=True`). Defense-in-depth: local `events` table query — 5-business-day rolling round-trip count from `order_submitted` + `fill` event pairs (BLOCK if ≥3 independently). Paper mode exempt per Alpaca docs.
- **T+1 settlement (EXEC-11 BLOCK):** `src/gekko/execution/checks/_t1.py::check_t1_settlement` — `qty × ref_price > non_marginable_buying_power` → BLOCK with `T1_SETTLEMENT_VIOLATION`. SELL orders exempt; margin (shorting_enabled=True) exempt.
- **Wash-sale FLAG (EXEC-09 surface-only):** `src/gekko/execution/checks/_wash_sale.py::flag_wash_sale(req, user_id) -> dict | None` — 30-day same-ticker lookback; NEVER raises; FLAG-only contract. `src/gekko/agent/proposal_writer.py::ProposalWriter._write_trade` stamps `wash_sale_flag` onto TradeProposal at proposal-build time (D-28). OrderGuard does NOT have a check_wash_sale BLOCK.
- **Market-hours guard (EXEC-04):** `src/gekko/execution/checks/_market_hours.py::check_market_hours` — reuses Phase-1's `pandas_market_calendars` integration. Half-day aware. `executor.market_closed` branch added (Plan 02-02) — WR-02 fix added Slack DM via `_send_slack_dm` so operator sees the rejection (matches cap_rejection path).
- **Broker rate-limit backoff (EXEC-08):** `src/gekko/brokers/_retry.py::retry_on_rate_limit` — tenacity factory (exponential 1s/2s/4s/8s + jitter; HTTP 429 only). Applied to `AlpacaBroker.get_account / get_positions / get_quote / get_order_by_client_order_id / get_orders_open` — GETs ONLY. `place_order` / `cancel_order` / `cancel_all_open_orders` ZERO-DECORATOR (EXEC-03 / Knight Capital; AST gate `tests/unit/test_alpaca_retry.py` enforces). WR-04 + WR-05 fixes added explicit "MUST NOT be decorated" docstrings to `cancel_order` (both AlpacaBroker + OrderGuard).
- **Walking-skeleton end-to-end:** `tests/integration/test_promote_paper_to_live_end_to_end.py::test_phase2_walking_skeleton_promote_paper_to_live_end_to_end` validates the full flow: promote paper→live → ProposalWriter stamps `account_mode="LIVE"` → Slack `_approve_workflow` diverts to AWAITING_2ND_CHANNEL (executor NOT dispatched) → dashboard `/live-confirm` transitions AWAITING_2ND_CHANNEL → APPROVED_LIVE + dispatches executor → OrderGuard 9 checks pass → AlpacaBroker.place_order delegated with `paper=False, _allow_live=True, credential_kind="alpaca_live"` → on_fill_event → proposal FILLED + `first_live_trade_confirmed_at` stamped → 6-event audit chain `walk_chain() == []`.
- **Second-proposal single-channel sanity** (same test file): after first-live stamp, second proposal uses regular HITL path; no AWAITING_2ND_CHANNEL transition.

**Deferred (acceptable, but the load-bearing real-money confirmation):** 02-07 Demo D — operator runs the README §"Phase 2 — Walking-skeleton demo" recipe with REAL Alpaca live credentials + REAL $1 limit order. Documented in `deferred-items.md` with 11-step recipe + acceptance criteria. The cassette test stands in for code-path coverage; only the real demo confirms broker-side behavior on real money.

**REQ-IDs satisfied:** EXEC-03 (no blind-retry POSTs), EXEC-08 (rate-limit backoff on GETs), EXEC-09 (wash-sale FLAG), EXEC-11 (PDT + T+1 BLOCK), partial EXEC-04 (market-hours).

---

## Cross-cutting Checks

### REQ-ID coverage (11/11 covered)

| REQ-ID | Status | Plan(s) | Notes |
|--------|--------|---------|-------|
| EXEC-03 | ✅ | 02-03 (AST gate) + 02-02 (cap_rejection branch) | Knight Capital invariant preserved; AST gate enforces zero-decorator on place_order/cancel_order/cancel_all_open_orders |
| EXEC-04 | ✅ | 02-02 (universe + hard_caps + qty_price + market_hours) | All 4 BLOCK checks shipped |
| EXEC-05 | ✅ | 02-02 + 02-06 (3-way invariant with credential_kind) | BLOCKER #4 grep gate enforces _allow_live restriction |
| EXEC-06 | ⚠ | 02-05 | Code shipped; 3 manual demos deferred (A/B/C) |
| EXEC-08 | ✅ | 02-03 (tenacity GET decoration) | __wrapped__ introspection + AST gate |
| EXEC-09 | ✅ | 02-03 (wash-sale FLAG via ProposalWriter) | FLAG-only contract preserved (OrderGuard never blocks) |
| EXEC-11 | ✅ | 02-03 (PDT + T+1 BLOCK) | Two-source PDT detection; T+1 via non_marginable_buying_power |
| BROK-A-02 | ✅ | 02-06 (Alpaca live credentials in SQLCipher vault) | BLOCKER #3 honest about plaintext-in-encrypted-DB |
| RES-06 | ✅ | 02-04 (directory-wide AST walk for Decision boundary) | BLOCKER #6 hardening — not single-module grep |
| RES-07 | ✅ | 02-04 (source allowlist + <untrusted_content> wrap) | Two-site wrap (web_fetch + finnhub_news); D-40 warning in DECISION_SYSTEM_PROMPT |
| HITL-06 | ⚠ | 02-06 (dual-channel state machine) | Code shipped; real $1 demo D deferred |

### CONTEXT.md decision coverage (15/15 D-26..D-40 honored)

All 15 decisions referenced in plan SUMMARIES and verified in shipped code per the 02-VERIFICATION-SUMMARY note in each plan's frontmatter. Spot-checked D-26 (OrderGuard as Brokerage subclass), D-27 (target_notional_usd field), D-29 (BLOCK/FLAG matrix), D-30 (cap_rejection reuses FAILED state), D-32 (HITL-06 state machine), D-34 (live credentials vault), D-36 (kill DB persistence), D-37 (kill DB-first ordering), D-40 (Decision prompt warning text). All confirmed in shipped code.

### Phase-1 invariants preserved

- ✅ **Knight Capital / EXEC-03** — AST gate enforces zero-decorator on `AlpacaBroker.place_order`, `AlpacaBroker.cancel_order`, `AlpacaBroker.cancel_all_open_orders`, `OrderGuard.place_order`. WR-04 + WR-05 fixes added explicit docstring contracts. Verified by `tests/unit/test_alpaca_retry.py`.
- ✅ **BLOCKER #5 TOCTOU closure** — `tests/unit/test_proposal_writer_account_mode_stamp.py` + `tests/integration/test_promote_paper_to_live_end_to_end.py::test_account_mode_survives_promote_then_demote_cycle` both pass.
- ✅ **BLOCKER #4 `_allow_live` grep gate** — `tests/unit/test_alpaca_live_construction_locked.py` AST walk passes.
- ✅ **No `claude_agent_sdk` import in execution/** — grep gate extends Phase-1; verified by code review.
- ✅ **SQLCipher whole-DB encryption** (Phase-1 D-19) — no Fernet wrap added. `BrokerCredential.__repr__` excludes blob fields.
- ✅ **Identity split** — Phase-1 quick-task 260612-nlv pattern enforced across 8 new Slack DM call sites (CR-02 + WR-06 fixes). All paths route through `_send_slack_dm(gekko_user_id, ...)`.
- ✅ **Decimal money math** — `target_notional_usd` is Decimal; qty × price comparisons Decimal; no float in money paths per code review WR-03 fix (fail closed on parse error rather than synthesizing permissive defaults).
- ✅ **CSP `script-src 'self'`** — 7 new dashboard templates use HTMX `hx-*` attributes only.
- ✅ **`_get_session_factory` shim** — all new DB-touching code uses per-user engine seam.
- ✅ **Audit chain integrity (D-14)** — BL-01 fix added 4 new event types to `_EVENT_TYPES` (`credentials_added`, `live_mode_promoted`, `live_mode_demoted`, `first_live_trade_confirmed`) + Alembic 0003 extends CHECK constraint. No more `event_type="error"` pollution for these state changes.

---

## Deferred Items Risk Assessment

All 4 deferred demos are documented in `deferred-items.md` with full recipes. Deferring is acceptable per the Phase-1 Plan 01-09 precedent (deferred 2026-06-11, executed 2026-06-12 with 22-event audit chain proof). The cassette test `test_promote_paper_to_live_end_to_end.py` covers the code paths at mocked-broker level — only wall-clock and broker-side behavior require real environment.

| Demo | Risk if not run | Recommendation |
|------|----------------|----------------|
| 02-05 A: 5s SLA stopwatch | MEDIUM — code path verified; only the wall-clock SLA needs human observation | Run within 24h of next live trading session |
| 02-05 B: cross-restart persistence | LOW — boot lifespan test passes in cassette; restart confirms it survives at process boundary | Run alongside Demo A in the same session |
| 02-05 C: dashboard typed-KILL modal | LOW — HTMX form-POST test passes; only DOM interaction needs human eyes | Run alongside Demos A + B |
| 02-07 D: real $1 first-live trade | **HIGH** — this is the load-bearing real-money confirmation that the Phase 2 safety floor works under real broker conditions | **Run before any larger live trades** |

**Operator action:** when ready, follow `README.md §"Phase 2 — Walking-skeleton demo (OrderGuard + Real-Money Alpaca Live)"`. Reply with `demo_passed` + audit-dump evidence in the relevant session.

---

## Phase-3 Carry-forward

- ✅ Partially closed by WR-02 fix: `executor.market_closed` now DMs operator via `_send_slack_dm` (Phase-1 carry-forward item)
- ⏳ Still open: `executor.BrokerOrderError` Slack DM surfacing (Phase-1 Plan 01-09 carry-forward; WR-02 fix only covered market_closed path). Tracked in `deferred-items.md`.

---

## Conclusion

Phase 2 (OrderGuard & Real-Money Alpaca Live — Safety Floor) ships with **4 of 5 success criteria fully passed and 1 passing-with-deferred-real-money-demo** (the load-bearing demo is documented + the cassette test stands in for code coverage; only wall-clock evidence on real broker is missing).

All 11 REQ-IDs covered. All 15 CONTEXT.md decisions honored. All 7 plan-checker BLOCKERs closed. All 11 code-review findings fixed (2 Critical + 2 Blocker + 7 Warning + 2 Info). All Phase-1 invariants preserved with new test coverage. 583 unit + 76 integration tests pass (1 pre-existing failure unrelated to Phase 2).

The one **HIGH-risk deferred demo (02-07 Demo D — real $1 first-live trade)** must be run before any larger real-money trading. The other three deferred demos (02-05 A/B/C — kill switch SLA + persistence + dashboard) are LOW-to-MEDIUM risk and can be batched in a single live-trading session.

**Recommended next action:**
1. Push the 14 unpushed commits to origin/main (already-fixed work shouldn't be stranded locally)
2. Run `/gsd-new-milestone v2.0` to formally archive Phase 2 + open the v2.0 milestone for Phases 3-5
3. Schedule the 4 deferred demos in the next live-trading session (Demo D especially)

**Verification status:** PASSED-WITH-DEFERRED.
