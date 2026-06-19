---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
verified: 2026-06-19T18:00:00Z
status: human_needed
score: 4/4
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 5/5
  gaps_closed:
    - "GAP 1 (edit-size redesign): _check_edit_size_caps replaces _drift_check as sole operator-edit gate on both Slack and dashboard paths; CR-01 mode-aware fail-closed wired; CR-02 division-by-zero guard wired"
    - "GAP 2 (broker-not-configured triage): root cause confirmed as market-closed guard (Scenario A); 'broker not configured' is architecturally absent from executor.py and routes.py; test_paper_approve_path and test_broker_not_configured_string_absent_from_executor_source added"
    - "GAP 3 (compact /approvals card): _proposal_card.html.j2 shows SIDE QTY TICKER + $cost + 1-line summary + collapsed details; cost formatted as $X,XXX.XX; 03-UI-SPEC.md Surface 2 updated with Compact Card Contract"
    - "GAP 4 (/approvals live refresh): GET /approvals/poll registered on authenticated router; hx-get=/approvals/poll hx-trigger='every 30s' wired on approvals_index; _proposals_list.html.j2 fragment created; modal-mount outside polling container"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Edit-size live behavior (cap validation in Slack modal)"
    expected: "47→50 shares passes; 47→500 shares shows 'That's above your max of $X (~N shares)'; plain-language framing 'Edit order size — BUY 47 AAPL (~$9,400.00)'"
    why_human: "Requires live Slack workspace; view_submission callback cannot be simulated without Slack credentials and a running socket connection"
  - test: "Dashboard fallback end-to-end approval flow"
    expected: "Approve from /approvals during market hours → proposal transitions APPROVED → EXECUTING → FILLED; audit log contains order_submitted + fill events; page polling surfaces status without reload"
    why_human: "Requires live executor, open market hours, and a running ASGI stack; cannot simulate execute_proposal + broker.place_order via grep"
  - test: "Quiet-hours queuing behavior over time"
    expected: "No Slack DM arrives during the quiet window; DM arrives when the window opens; executor_error DMs (expiry, kill) still fire during quiet hours"
    why_human: "Real-time behavior over 2+ hours cannot be verified statically"
  - test: "Daily P&L digest at 16:30 ET on a NYSE trading day"
    expected: "Block Kit digest shows gross P&L (BUYs subtract, SELLs add), per-strategy breakdown by strategy name — no _unknown_ buckets, no sign-flipped SELLs"
    why_human: "Requires real fill events from a live or paper trading session; static analysis confirms the implementation is correct but cannot produce actual fills"
---

# Phase 03: Production HITL UX — Gap-Closure Re-Verification Report

**Phase Goal:** User has a production-grade approval surface — idempotent Slack buttons that survive at-least-once delivery, configurable quiet hours, timeout=REJECT default, edit-size and escalate-to-dashboard options, stale-proposal expiry, dashboard fallback, and a daily P&L digest with severity-tier executor-error DMs.
**Verified:** 2026-06-19T18:00:00Z
**Status:** human_needed
**Re-verification:** Yes — gap-closure plans 03-11, 03-12, 03-13 (verifying 4 OPEN UAT gaps)

## Re-verification Scope

This is a targeted re-verification of the 4 OPEN gaps from 03-HUMAN-UAT.md. The previous verification (2026-06-18T14:00:00Z, status: human_needed, score 5/5) confirmed all core truths. The gap-closure plans 03-11/12/13 addressed the 4 remaining open gaps. Previously-verified surfaces receive regression checks only.

## Gap Closure Verification

### GAP 1: Edit-size redesign (Plan 03-11)

**Required truth:** `_check_edit_size_caps` in `src/gekko/approval/actions.py` validates operator edits against OrderGuard hard caps (`max_position_pct * account_equity`), NOT the 2% target-notional drift. Wired into both Slack and dashboard paths. Mode-aware fail-closed for LIVE proposals (CR-01). Division-by-zero guard on zero `target_notional_usd` in audit event (CR-02).

**Verification findings:**

1. `src/gekko/approval/actions.py` — `_check_edit_size_caps(qty, ref_price, strategy, account_equity) -> tuple[bool, str]` exists at lines 63-119. Logic is Decimal-exact. Returns `(False, "Quantity must be at least 1 share.")` for qty <= 0. Returns `(False, "That's above your max of ${max_order_notional:,.2f} (~{max_shares_approx} shares) — pick a smaller number.")` when new_notional > max_order_notional. Fail-open when account_equity == 0. `_drift_check` preserved unchanged for agent-output validation. Status: VERIFIED

2. `src/gekko/approval/slack_handler.py` — `handle_edit_size_view_submission` imports `_check_edit_size_caps` from `gekko.approval.actions` (confirmed line 733). `_drift_check` appears only in a docstring comment ("why _drift_check is wrong here"), NOT in the submission handler body. Cap check called at line 858: `ok, cap_msg = _check_edit_size_caps(new_qty, ref_price, strategy, equity)`. Status: VERIFIED

3. `src/gekko/dashboard/routes.py` — `edit_size_submit` imports `_check_edit_size_caps` (line 586). Comment documents "_drift_check is NOT applied" (lines 576-578). Cap check called at line 767: `_ok, _cap_msg = _check_edit_size_caps(new_qty, ref_price, strategy_obj, equity)`. Status: VERIFIED

4. **CR-01 (mode-aware fail-closed):** Both paths implement `if strategy is None:` check with mode-aware branching. LIVE proposals return an explicit cap-load-failed error to the operator; PAPER proposals remain fail-open (with OrderGuard as backstop). Confirmed at `slack_handler.py:833-858` and `routes.py:731-766`. Status: VERIFIED

5. **CR-02 (division-by-zero guard):** `_edit_size_submit_workflow` in `slack_handler.py` now guards the `drift_pct` audit field computation at lines 932-940: `_drift_pct = abs(...) / _target_notional if _target_notional > Decimal("0") else Decimal("0")`. The `InvalidOperation` crash on market orders with `target_notional_usd == "0"` is fixed. Status: VERIFIED

6. `src/gekko/dashboard/templates/edit_size_modal.html.j2` — Modal headline is `Edit order size — {{ side }} {{ qty }} {{ ticker }} (~${{ original_notional }})` (line 30). Help text reads "Ref price: / Current order: / Adjust quantity below. Max order size is enforced at submit." The `drift_error` div still present, reused for cap messages. "Drift > 2% will be rejected" copy removed. Status: VERIFIED

7. `tests/unit/test_edit_size_caps.py` — 6 tests present: `test_pass_within_cap`, `test_fail_exceeds_cap`, `test_fail_zero_qty`, `test_fail_negative_qty`, `test_pass_exact_cap`, `test_zero_equity_skip`. All cover the required acceptance criteria. Status: VERIFIED

8. `03-UI-SPEC.md` — Contains `"That's above your max"` in Surface 1 and error copy table (confirmed via grep). `_drift_check is NOT called for operator edits` noted. Status: VERIFIED

9. `03-CONTEXT.md` — D-54 contains `_check_edit_size_caps` (line 75). UAT finding 2026-06-19 documented. Two-layer defense (cap check + OrderGuard) explained. Status: VERIFIED

**GAP 1 STATUS: VERIFIED**

### GAP 2: Broker-not-configured triage (Plan 03-12)

**Required truth:** "broker not configured" string is confined to `src/gekko/agent/tools/alpaca_data.py` (Researcher path get_quote fallback). It does NOT appear in `src/gekko/execution/executor.py` or `src/gekko/dashboard/routes.py`. Paper approve path reaches execution with a configured broker.

**Verification findings:**

1. `grep "broker not configured" src/gekko/execution/executor.py` — 0 matches. Status: VERIFIED

2. `grep "broker not configured" src/gekko/dashboard/routes.py` — 0 matches. Status: VERIFIED

3. `tests/unit/test_executor.py` — `test_paper_approve_path_executes_without_broker_not_configured_error` exists at line 686: monkeypatches `is_market_open` to True, confirms paper APPROVED proposal reaches EXECUTING without BrokerOrderError. `test_broker_not_configured_string_absent_from_executor_source` exists at line 747: architectural grep gate on source bytes of both files. Status: VERIFIED

4. Root cause documented in 03-12-SUMMARY.md as Scenario A (market closed): the observed UAT failure was `is_market_open()` returning False during off-hours testing, not a code wiring gap. `_build_broker` constructs `AlpacaBroker(paper=True)` correctly from settings credentials. Status: VERIFIED (documented)

**GAP 2 STATUS: VERIFIED**

### GAP 3: Compact /approvals card (Plan 03-13)

**Required truth:** `_proposal_card.html.j2` shows `SIDE QTY TICKER` + `$cost` + 1-line summary + collapsed details. Cost formatted as `$X,XXX.XX`. `03-UI-SPEC.md` Surface 2 documents the Compact Card Contract.

**Verification findings:**

1. `src/gekko/dashboard/templates/_proposal_card.html.j2` — Line 30: `<span class="proposal-card-ticker">{{ side }} {{ qty }} {{ ticker }}</span>`. Line 33: `<span class="proposal-card-cost" aria-label="estimated cost">{{ cost }}</span>`. Lines 47-49: `{% if summary %}<div class="proposal-card-summary">{{ summary }}</div>{% endif %}`. Lines 52-64: `<details class="proposal-card-details">` wraps full rationale + evidence, collapsed by default. Status: VERIFIED

2. `src/gekko/dashboard/routes.py` `_build_proposal_ctx` — Line 227: `cost = f"${Decimal(str(cost_raw)):,.2f}" if cost_raw not in (None, "") else ""`. `$X,XXX.XX` format confirmed. Status: VERIFIED

3. `03-UI-SPEC.md` Surface 2 — `"Compact Card Contract"` found at line 398: "The card is scannable at-a-glance: SIDE/QTY/TICKER is the action, $cost is the exposure, the 1-line summary is the why. Full rationale/evidence are secondary — collapsed by default under `<details>`. This layout was prototyped live during UAT 2026-06-19 and formalized here." Status: VERIFIED

**GAP 3 STATUS: VERIFIED**

### GAP 4: /approvals live refresh (Plan 03-13)

**Required truth:** `GET /approvals/poll` route registered on the authenticated `router` (inherits `require_session`). `approvals_index.html.j2` has `hx-get="/approvals/poll"` and `hx-trigger="every 30s"` on the proposal list container. `_proposals_list.html.j2` fragment exists and contains the proposal loop. `modal-mount` div is outside the polling container.

**Verification findings:**

1. `src/gekko/dashboard/routes.py` — `@router.get("/approvals/poll", response_class=HTMLResponse)` declared at line 289. Route handler signature: `async def approvals_poll(request: Request, user_id: str = Depends(require_session))`. Registered on `router` (the authenticated `APIRouter(dependencies=[Depends(require_session)])`) — not on `public_router`. Status: VERIFIED

2. `src/gekko/dashboard/templates/approvals_index.html.j2` — Lines 19-25 confirmed:
   ```
   <div id="proposals-list-container"
        hx-get="/approvals/poll"
        hx-trigger="every 30s"
        hx-target="#proposals-list-container"
        hx-swap="innerHTML">
     {% include "_proposals_list.html.j2" %}
   </div>
   ```
   Line 29: `<div id="modal-mount"></div>` — **outside** the polling container div (closed at line 25). Status: VERIFIED

3. `src/gekko/dashboard/templates/_proposals_list.html.j2` — File exists. Contains the `{% if proposals %} / {% for proposal in proposals %} / {% with ... %} {% include "_proposal_card.html.j2" %}` block plus empty-state div. Fragment only — does not extend `base.html.j2`. Status: VERIFIED

4. WR-02 note: `approvals_poll` declares `Depends(require_session)` explicitly to capture `user_id`, in addition to the router-level dependency. This is the standard pattern used by all other routes on this router. The double-invocation is benign (each call reads `request.session` — no side effects). The 03-REVIEW.md WR-02 recommendation was to add a clarifying comment; this is informational only and does not block the truth.

**GAP 4 STATUS: VERIFIED**

## Observable Truths (Full Phase — Regression Check)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Idempotent Slack approve/reject/edit-size/escalate buttons; at-least-once delivery = exactly one action | VERIFIED (regression: unchanged) | `claim_action` UNIQUE-INSERT sole dedup primitive; `_extract_retry_num` absent; 9 occurrences of `claim_action` in slack_handler.py — verified in prior run, no changes to dedup.py or slack_handler.py dedup path |
| 2 | Quiet hours configurable; proposals queue during window, delivered when window opens | VERIFIED (regression: unchanged) | quiet_hours.py + _send_slack_dm_respecting_quiet_hours wiring unchanged; executor_error bypass confirmed; no modifications to these files in 03-11/12/13 |
| 3 | Proposal expires after configurable timeout (default 30 min), auto-rejects with non-suppressible notification | VERIFIED (regression: unchanged) | expiry.py + scheduler wiring unchanged; category="executor_error" confirmed in prior run |
| 4 | Operator can edit order size and approve in single interaction with audit record; uses OrderGuard hard caps (not 2% drift) | VERIFIED | Plans 03-11 gap closure — see GAP 1 above |
| 5 | Dashboard /approvals: same approve/reject/edit flow works identically when Slack unavailable; live proposals surface without reload | VERIFIED | Plan 03-12 (broker triage confirmed correct wiring) + Plan 03-13 (HTMX polling) — see GAP 2/4 above |

**Score:** 4/4 gap-closure truths verified; 5/5 full-phase truths passing

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/gekko/approval/actions.py` | `_check_edit_size_caps` helper | VERIFIED | 57-line substantive implementation; Decimal-exact; `_drift_check` preserved unchanged |
| `tests/unit/test_edit_size_caps.py` | 6 cap-check unit tests | VERIFIED | All 6 tests as specified in plan: pass/fail/zero/negative/exact-boundary/zero-equity |
| `src/gekko/approval/slack_handler.py` | Cap check wired; drift check removed from submission handler; CR-01 mode-aware fail-closed; CR-02 division guard | VERIFIED | `_check_edit_size_caps` imported and called; `_drift_check` in comments only (not called); `if strategy is None` mode-aware rejection at line 833; division guard at lines 932-940 |
| `src/gekko/dashboard/routes.py` | Cap check wired in edit_size_submit; GET /approvals/poll on authenticated router | VERIFIED | `_check_edit_size_caps` called at line 767; `if strategy_obj is None` mode-aware rejection at line 731; poll route at line 289 on `router` |
| `src/gekko/dashboard/templates/edit_size_modal.html.j2` | Plain-language framing SIDE QTY TICKER ~$total | VERIFIED | Headline line 30 confirmed; help text updated; drift copy removed |
| `src/gekko/dashboard/templates/approvals_index.html.j2` | HTMX polling container + modal-mount outside | VERIFIED | hx-get, hx-trigger="every 30s", hx-target, hx-swap all present; modal-mount outside container |
| `src/gekko/dashboard/templates/_proposals_list.html.j2` | Fragment with proposal loop; does not extend base | VERIFIED | File exists; `{% for proposal in proposals %}` + `{% with %}` unpack + `{% include "_proposal_card.html.j2" %}` pattern; no extends |
| `src/gekko/dashboard/templates/_proposal_card.html.j2` | Compact card: ticker/cost/summary/details | VERIFIED | `proposal-card-ticker`, `proposal-card-cost`, `proposal-card-summary`, `<details>` all present |
| `tests/unit/test_executor.py` | Paper approve + architectural grep gate tests | VERIFIED | `test_paper_approve_path_executes_without_broker_not_configured_error` at line 686; `test_broker_not_configured_string_absent_from_executor_source` at line 747 |
| `03-UI-SPEC.md` Surface 1 | "That's above your max" error copy; _drift_check NOT called note | VERIFIED | Error string found in error copy table and error block example |
| `03-UI-SPEC.md` Surface 2 | "Compact Card Contract" prose note | VERIFIED | Found at line 398 |
| `03-CONTEXT.md` D-54 | `_check_edit_size_caps` as sole operator-edit gate; UAT finding documented | VERIFIED | Line 75 and 78 confirmed |

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `slack_handler.py handle_edit_size_view_submission` | `actions.py _check_edit_size_caps` | explicit import + call | WIRED | `from gekko.approval.actions import _check_edit_size_caps` at line 733; called at line 858 |
| `routes.py edit_size_submit` | `actions.py _check_edit_size_caps` | explicit import + call | WIRED | `from gekko.approval.actions import _check_edit_size_caps` at line 586; called at line 767 |
| `approvals_index.html.j2` polling container | `routes.py GET /approvals/poll` | `hx-get="/approvals/poll"` | WIRED | hx-get attribute present; route exists on authenticated router at line 289 |
| `_proposals_list.html.j2` | `approvals_index.html.j2` | `{% include "_proposals_list.html.j2" %}` | WIRED | Include present inside polling container |

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `_proposal_card.html.j2` `{{ cost }}` | `cost` | `_build_proposal_ctx` → `f"${Decimal(str(cost_raw)):,.2f}"` | YES — Decimal-formatted from `payload.get("target_notional_usd")` | FLOWING |
| `_proposal_card.html.j2` `{{ summary }}` | `summary` | `_build_proposal_ctx` → `rationale.strip().replace("\n", " ")[:140]` | YES — truncated rationale from proposal payload | FLOWING |
| `_check_edit_size_caps` `account_equity` | broker.get_account() | async `asyncio.wait_for(broker.get_account(), timeout=2.5)` in both Slack and dashboard callers | YES — fetched from Alpaca paper API; fail-open on equity=0 | FLOWING |

## Behavioral Spot-Checks

Step 7b: SKIPPED — no runnable entry points accessible without active SQLCipher DB, Slack credentials, and market-hours timing. The unit tests in `test_edit_size_caps.py` (6 tests) and `test_executor.py` (2 new tests) provide equivalent behavioral coverage for the gap-closure changes.

## Probe Execution

No probe-*.sh files defined for Phase 3.

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| HITL-02 | 03-02, 03-10 | Slack buttons idempotent — at-least-once delivery cannot cause double-execution | SATISFIED | Unchanged from prior verification; `claim_action` UNIQUE-INSERT confirmed sole dedup primitive |
| HITL-03 | 03-04, 03-09 | Timeout = REJECT default; proposals expire after 30 min configurable | SATISFIED | Unchanged from prior verification; CR-04 closed (executor_error category) |
| HITL-04 | 03-05, 03-11 | User can approve, reject, edit-size from Slack card | SATISFIED | Edit-size now uses OrderGuard hard caps (Plan 03-11); drift check removed from operator path |
| HITL-05 | 03-03 | Quiet hours configurable; no 2am pings | SATISFIED | Unchanged from prior verification |
| DASH-04 | 03-05, 03-08, 03-12, 03-13 | Dashboard approval fallback | SATISFIED | Broker triage confirmed executor path correct (03-12); HTMX polling adds live refresh (03-13) |
| REPT-01 | 03-06, 03-09 | Slack DM for proposals, executions, daily P&L, errors | SATISFIED | Unchanged from prior verification |

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/gekko/approval/actions.py` | 27 | `__all__` exports private names (`_check_edit_size_caps`, `_drift_check`) | INFO | Not a risk — modules use explicit imports, not `*`. Noted in 03-REVIEW.md IN-01. No change needed for correctness. |
| `src/gekko/dashboard/templates/approvals_index.html.j2` | 19-25 | HTMX poll replaces container while operator may be clicking a button (mid-flight DOM swap) | INFO | Noted in 03-REVIEW.md IN-02. Not a data-correctness issue (dedup prevents double-execution); can produce confusing UX if button click races the 30s poll. Low-frequency concern for a single-operator localhost deployment. |
| `src/gekko/dashboard/routes.py` | 289-292 | `require_session` applied at both router level and poll route handler level (WR-02) | INFO | Benign double-invocation; both calls read request.session. Pattern is consistent with all other routes on this router that capture `user_id`. No security or correctness concern. |

No TBD, FIXME, or XXX debt markers found in any file modified by plans 03-11, 03-12, or 03-13.

## Human Verification Required

### 1. Edit-size live behavior (cap validation in Slack modal)

**Test:** Click edit-size on a pending proposal card in Slack. Try (a) 47→50 shares (within cap) and (b) 47→500 shares (exceeds cap). Submit each.
**Expected:** (a) Modal closes, card updates to APPROVED, executor fires. (b) Modal re-renders with "That's above your max of $X (~N shares) — pick a smaller number." in red. Modal title reads "Edit order size — BUY 47 AAPL (~$9,400.00)".
**Why human:** Requires live Slack workspace with socket connection; view_submission callback cannot be simulated without Slack credentials.

### 2. Dashboard fallback end-to-end approval flow

**Test:** Navigate to /approvals during NYSE market hours. Approve a pending paper proposal.
**Expected:** Proposal transitions from PENDING to APPROVED, executor fires in background, proposal card updates to FILLED on next 30s poll refresh. No "broker not configured" error in logs.
**Why human:** Requires live executor, open market hours, running ASGI stack. The market-closed guard will produce FAILED if tested outside NYSE hours — that is expected correct behavior, not a bug (documented root cause of UAT observation).

### 3. Quiet-hours queuing behavior over time

**Test:** Configure quiet hours covering the current time. Trigger a strategy run. Wait until the quiet window closes.
**Expected:** No Slack DM arrives during the quiet window; DM arrives when the window opens. Safety-critical categories (kill, executor errors, expiry) still fire during quiet hours.
**Why human:** Real-time behavior over 2+ hours cannot be verified statically.

### 4. Daily P&L digest at 16:30 ET on a NYSE trading day

**Test:** On a NYSE trading day, trigger fills for a strategy with BUY and SELL fills. Observe the 16:30 ET APScheduler cron DM.
**Expected:** Block Kit digest shows correct gross P&L (BUYs subtract, SELLs add), per-strategy breakdown by strategy name — no `_unknown_` buckets, no sign-flipped SELLs.
**Why human:** Requires real fill events from a live session; static analysis confirms the implementation but cannot produce fill events to observe.

## Gaps Summary

All 4 OPEN UAT gaps are now closed by plans 03-11, 03-12, 03-13:

1. **Edit-size redesign (GAP 1):** `_check_edit_size_caps` is the sole operator-edit gate on both Slack and dashboard paths. `_drift_check` removed from both submission handlers. CR-01 mode-aware fail-closed prevents silent cap bypass when strategy is unavailable. CR-02 prevents `InvalidOperation` crash on market-order edits. 6 unit tests confirm the cap math. 03-UI-SPEC.md Surface 1 and 03-CONTEXT.md D-54 document the new contract.

2. **Broker-not-configured triage (GAP 2):** Confirmed Scenario A (market closed) as the root cause. The string "broker not configured" is architecturally absent from the executor path. Two new tests in `test_executor.py` prove this as a CI-enforceable architectural assertion.

3. **Compact /approvals card (GAP 3):** Card shows SIDE/QTY/TICKER + $cost chip + 1-line summary + collapsed details. Cost formatted with `$X,XXX.XX`. `03-UI-SPEC.md` Surface 2 contains the Compact Card Contract prose.

4. **Live refresh (GAP 4):** `GET /approvals/poll` on authenticated router. HTMX `hx-trigger="every 30s"` polling container in `approvals_index.html.j2`. `_proposals_list.html.j2` fragment serves the partial. `modal-mount` div is outside the polling container (edit-size modal unaffected by poll refreshes).

No automated blockers or failures. 4 human verification items remain — behavioral/live checks that require market hours, Slack credentials, or a running ASGI stack. These were identified in the prior verification and are unchanged in scope.

---

_Verified: 2026-06-19T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification: Yes — gap-closure plans 03-11, 03-12, 03-13_
