---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
verified: 2026-06-23T00:00:00Z
status: human_needed
score: 6/6
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 7/7
  gaps_closed:
    - "Bug B (duplicate edit-submit InvalidRequestError): edit_size_submit duplicate branch now re-reads via fresh sf4/engine4 session OUTSIDE the sf3 begin() block — no .execute() on rolled-back session2"
    - "Bug A (unstyled Slack deeplink): edit_size_get checks HX-Request header; non-HX direct-nav returns RedirectResponse(302, /approvals?open_edit={id}); HTMX path returns bare fragment unchanged"
    - "Bug C (terminal-state ValueError 500): _TERMINAL_STATUSES frozenset guards all three action endpoints (approve_proposal_endpoint, reject_proposal_endpoint, edit_size_submit) before transition_status; returns HTTP 200 with current card"
    - "Terminal-state card chips: _proposal_card.html.j2 action buttons already PENDING-only; explicit status chips added for APPROVED/APPROVED_LIVE/EXECUTING, FILLED, REJECTED, FAILED"
    - "Real-SQLite integration test (test_dashboard_hitl_actions.py): 8/8 passing; claim_action/transition_status/append_event run unmocked against real SQLCipher engine"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Repeat-click edit-size in live browser (Bug B browser retest)"
    expected: "Click 'Edit size' on a pending proposal, submit. Click 'Edit size' on the same proposal again and submit. Second response is a styled card — not a 500 error page."
    why_human: "Test 5 in test_dashboard_hitl_actions.py covers this programmatically and passes; browser UAT confirms the full HTMX card-swap UX including visual rendering and button disabling."
  - test: "Slack deep-link 'Edit size' URL in browser (Bug A browser retest)"
    expected: "Open the Slack URL-button link (format: http://localhost:8000/approvals/{id}/edit-size) directly in a browser tab. Page should redirect to /approvals and show the styled dashboard with CSS and nav — not a naked HTML partial."
    why_human: "Test 8 asserts the 302 redirect programmatically; browser confirms the user actually lands on a styled page after the redirect."
  - test: "Terminal-state proposal action in browser (Bug C browser retest)"
    expected: "With an already-FILLED (or APPROVED/REJECTED) proposal visible on /approvals, clicking Approve/Edit size returns a styled card showing the terminal-state chip — not a 500 Internal Server Error page."
    why_human: "Tests 3 and 6 cover this programmatically; browser confirms the HTMX swap renders the correct chip and no error is surfaced."
  - test: "Slider live readout — drag interaction"
    expected: "Dragging the range slider handle updates #size-readout in real time: 'N shares approximately $X,XXX.XX — Y.Z% of your $X,XXX.XX'. On equity-fetch-failure the readout shows 'N shares' only."
    why_human: "Static analysis confirms the delegated 'input' listener and htmx:afterSettle hook are wired in edit-size-slider.js and no inline oninput attribute is present; the drag-to-readout interaction requires a live browser with the ASGI stack running."
  - test: "Dashboard approve-after-edit end-to-end during market hours"
    expected: "Operator adjusts slider, submits 'Approve at this size', proposal transitions APPROVED then EXECUTING then FILLED; audit log contains edit_size event with old_qty/new_qty, then order_submitted and fill events."
    why_human: "Requires live executor, open market hours, and a running ASGI stack with a configured Alpaca paper account."
  - test: "Quiet-hours queuing behavior over time"
    expected: "No Slack DM arrives during the quiet window; DM arrives when the window opens; safety-critical categories (kill, executor errors, first-live fills) still fire during quiet hours."
    why_human: "Real-time behavior over 2+ hours cannot be verified statically."
  - test: "Daily P&L digest at 16:30 ET on a NYSE trading day"
    expected: "Block Kit digest shows gross P&L (BUYs subtract, SELLs add), per-strategy breakdown by strategy name — no _unknown_ buckets, no sign-flipped SELLs."
    why_human: "Requires real fill events from a live or paper trading session; static analysis confirms the implementation is correct but cannot produce actual fills."
---

# Phase 03: Production HITL UX — Verification Report (Post Plan 03-15)

**Phase Goal:** Production HITL UX — idempotent Slack/dashboard approval flow, quiet hours, timeout=REJECT, edit-size, dashboard fallback.
**Verified:** 2026-06-23T00:00:00Z
**Status:** human_needed
**Re-verification:** Yes — targeted re-verification after gap-closure plan 03-15 (3-bug cluster in dashboard HITL action endpoints). Plans 03-01 through 03-14 were verified in the prior VERIFICATION.md (score 7/7, status human_needed). This pass focuses exclusively on the 6 must-haves from 03-15-PLAN.md frontmatter. Previously-verified truths (Plans 03-01 through 03-14) received no regression check — they were confirmed passing in the prior verification and no files they own were touched by 03-15.

---

## Goal Achievement

### Observable Truths (Plan 03-15 must-haves)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Duplicate edit-size click returns HTTP 200 with current card — never a 500 InvalidRequestError | VERIFIED | `routes.py` line 1029-1034: `else: pass` (no re-read inside rolled-back session2). Lines 1039-1055: fresh `sf4, engine4 = _get_session_factory(user_id)` block outside the sf3 begin() for `outcome == "duplicate" or already_terminal`. Test 5 (`test_edit_submit_duplicate_returns_200`) asserts `resp2.status_code == 200` and `"InvalidRequestError" not in resp2.text`. |
| 2 | GET /approvals/{id}/edit-size without HX-Request returns full page or 302; with HX-Request: true returns bare fragment | VERIFIED | `routes.py` lines 685-689: `is_htmx = request.headers.get("HX-Request") == "true"` check; non-HX branch returns `RedirectResponse(url=f"/approvals?open_edit={proposal_id}", status_code=302)`. HTMX path falls through to existing `TemplateResponse` (bare fragment). Test 8 asserts 302 with `/approvals` in Location header for no-header case, and 200 + no `<!DOCTYPE` for HTMX case. |
| 3 | POST approve / reject / edit-submit against terminal proposal returns HTTP 200 with current card — never a ValueError 500 | VERIFIED | `routes.py` line 61-70: `_TERMINAL_STATUSES` frozenset defined at module level. Lines 399-401: `approve_proposal_endpoint` guard `if row.status in _TERMINAL_STATUSES: already_terminal = True`. Lines 485-487: same guard in `reject_proposal_endpoint`. Lines 978-980: same guard in `edit_size_submit`. Tests 3 and 6 assert `resp.status_code == 200` and `"ValueError" not in resp.text` on FILLED seed. |
| 4 | Terminal-state proposal cards do not render action buttons | VERIFIED | `_proposal_card.html.j2` lines 71-117: action buttons (`Approve`, `Reject`, `Edit size`) are exclusively inside `{% if status == "PENDING" %}`. All terminal statuses (EXPIRED, AWAITING_2ND_CHANNEL, APPROVED/APPROVED_LIVE/EXECUTING, FILLED, REJECTED, FAILED) render read-only status chips inside `{% elif %}` blocks with no interactive elements. |
| 5 | Real-SQLite integration test passes: 8/8 cases, no claim_action/transition_status/append_event mocks | VERIFIED | `tests/integration/test_dashboard_hitl_actions.py` exists (717 lines). Design contract stated in header comments line 14-19: "claim_action, transition_status, and append_event are NOT mocked." Grep confirms zero `patch.*claim_action`, `patch.*transition_status`, or `AsyncMock.*transition` in the file. `append_event` is imported directly from `gekko.audit.log` and called in the seed helper (line 171) — not mocked in route handlers. Orchestrator-reported result: 8/8 passed with real SQLCipher. |
| 6 | Existing edit-size, OrderGuard, and walking-skeleton integration suites remain green | VERIFIED | `_check_edit_size_caps` import in `routes.py` line 730 unchanged; `edit_size_submit` still calls it as the sole cap gate (line 919). SUMMARY documents `test_dashboard_edit_size_happy.py` updated with `HX-Request: true` header (Rule 1 auto-fix for the new Bug A guard) and all pre-existing suites passed in isolation per orchestrator evidence. |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/gekko/dashboard/routes.py` | `_TERMINAL_STATUSES`, Bug A/B/C fixes in all three endpoints | VERIFIED | `_TERMINAL_STATUSES` frozenset at lines 61-70; Bug C guard in `approve_proposal_endpoint` (lines 399-401), `reject_proposal_endpoint` (lines 485-487), `edit_size_submit` (lines 978-980); Bug B `pass`-and-fresh-session pattern at lines 1029-1055; Bug A `HX-Request` check at lines 685-689. |
| `src/gekko/dashboard/templates/_proposal_card.html.j2` | Action buttons PENDING-only; terminal status chips for all non-PENDING states | VERIFIED | Lines 71-117: `{% if status == "PENDING" %}` contains all interactive buttons; explicit `{% elif %}` branches cover EXPIRED (line 93), AWAITING_2ND_CHANNEL (line 98), APPROVED/APPROVED_LIVE/EXECUTING (lines 101-104), FILLED (lines 105-108), REJECTED (lines 109-112), FAILED (lines 113-116). |
| `tests/integration/test_dashboard_hitl_actions.py` | 8-test real-SQLite integration suite, no dedup/transition mocks | VERIFIED | File exists, 717 lines. Eight `@pytest.mark.asyncio` test functions present covering all required coverage cases. No mocks of `claim_action`, `transition_status`, or `append_event`. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `edit_size_submit` duplicate branch | `_get_session_factory` fresh session outside `sf3.begin()` | `sf4, engine4 = _get_session_factory(user_id)` after `engine3.dispose()` | VERIFIED | Lines 1039-1055: fresh factory opened only when `outcome == "duplicate" or already_terminal`. No `.execute()` on session2 after rollback. |
| `edit_size_get` non-HX path | `/approvals?open_edit={id}` redirect | `request.headers.get("HX-Request") == "true"` check | VERIFIED | Lines 685-689: conditional on `is_htmx`; non-HX returns 302 RedirectResponse. |
| `approve_proposal_endpoint` / `reject_proposal_endpoint` / `edit_size_submit` | `_TERMINAL_STATUSES` guard before `transition_status` | `if row.status in _TERMINAL_STATUSES: already_terminal = True` | VERIFIED | All three endpoints confirmed at lines 399-401, 485-487, 978-980 respectively. |

---

### Data-Flow Trace (Level 4)

Not re-run for this targeted re-verification. Plan 03-14 data-flow traces were VERIFIED in the previous VERIFICATION.md (all FLOWING). The 03-15 bug fixes do not alter any data-source wiring — they add guards before state transitions and move a DB re-read to a fresh session. No new dynamic data rendering was introduced.

---

### Behavioral Spot-Checks

| Behavior | Evidence | Status |
|----------|----------|--------|
| `_TERMINAL_STATUSES` frozenset defined in routes.py | Lines 61-70: `frozenset({"FILLED","EXPIRED","REJECTED","FAILED","APPROVED","APPROVED_LIVE","EXECUTING","AWAITING_2ND_CHANNEL"})` | PASS |
| Bug A redirect: `HX-Request` check in `edit_size_get` | Lines 685-689 confirmed in source | PASS |
| Bug B fresh-session: `else: pass` inside `session2.begin()` | Line 1034 confirmed; fresh `sf4` block at lines 1041-1055 | PASS |
| Bug C guard: all 3 endpoints have `_TERMINAL_STATUSES` check | Lines 399-401 (approve), 485-487 (reject), 978-980 (edit_size_submit) | PASS |
| No action buttons outside `{% if status == "PENDING" %}` | `_proposal_card.html.j2` reviewed — no interactive elements outside the PENDING block | PASS |
| `_check_edit_size_caps` unchanged as sole server-side gate | `routes.py` line 919: `_ok, _cap_msg = _check_edit_size_caps(new_qty, ref_price, strategy_obj, equity)` — unchanged | PASS |
| No `claim_action`/`transition_status` mocks in test file | Grep on test_dashboard_hitl_actions.py: zero matches for `patch.*claim_action`, `AsyncMock.*transition` | PASS |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/gekko/dashboard/routes.py` | 1389 | `"version": 1,  # placeholder; next_version overrides` | Info | Pre-existing comment; version field is immediately overridden in the same call. Not a behavioral placeholder. Not in 03-15 scope. |
| `src/gekko/dashboard/routes.py` | 1584 | `# Return an empty kill-banner-mount placeholder so HTMX swaps the` | Info | Pre-existing CSS mount comment. Not a stub. Not in 03-15 scope. |

No TBD, FIXME, or XXX markers found in any file modified by Plan 03-15.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| HITL-02 | 03-15 | Idempotent Slack/dashboard buttons — dedup + re-read for duplicate path | VERIFIED | Bug B fix ensures duplicate path re-reads via fresh session; dedup UNIQUE constraint unchanged. |
| HITL-04 | 03-15 | Edit-size legibility and correct endpoint behavior | VERIFIED | Bug A (styled deeplink redirect), Bug B (duplicate 200), Bug C (terminal guard) all fix correctness gaps in the edit-size flow. |
| DASH-04 | 03-15 | Dashboard approval fallback — all action endpoints return valid HTML, not 500 | VERIFIED | All three action endpoints return HTTP 200 with current proposal card for duplicate and terminal-state cases. |

---

### Human Verification Required

The following items require a live browser or real broker/market-hours conditions. Automated static and programmatic analysis cannot substitute.

#### 1. Repeat-click edit-size in live browser (Bug B browser retest)

**Test:** Click "Edit size" on a pending proposal, submit. Click "Edit size" on the same proposal again and submit.
**Expected:** Second response is a styled proposal card — not a 500 error page or "Internal Server Error" text.
**Why human:** Test 5 covers this programmatically and passes (8/8 confirmed by orchestrator). Browser UAT confirms the full HTMX card-swap UX including visual rendering and `hx-disable-elt` button disabling behavior.

#### 2. Slack deep-link "Edit size" URL in browser (Bug A browser retest)

**Test:** Copy the Slack URL button link (format: `http://localhost:8000/approvals/{id}/edit-size`) and open it directly in a browser tab (not via HTMX).
**Expected:** Page redirects to `/approvals` and shows the styled dashboard with CSS and nav — not a naked HTML partial.
**Why human:** Test 8 asserts the 302 redirect programmatically. Browser confirms the user actually lands on a styled page and can interact with the approvals dashboard after the redirect.

#### 3. Terminal-state proposal action in browser (Bug C browser retest)

**Test:** With an already-FILLED (or APPROVED/REJECTED) proposal visible on `/approvals`, click Approve or Edit size.
**Expected:** Returns a styled card showing the terminal-state status chip (e.g., "Filled — order executed.") — not a 500 Internal Server Error page.
**Why human:** Tests 3 and 6 cover this programmatically. Browser UAT confirms the HTMX swap renders the correct chip visually and no error is surfaced to the operator.

#### 4. Slider live readout — drag interaction (carried forward from Plan 03-14)

**Test:** Open `/approvals` in a browser, trigger the edit-size modal for a PENDING proposal, and drag the range slider handle.
**Expected:** The `#size-readout` element updates in real time: "N shares approximately $X,XXX.XX — Y.Z% of your $X,XXX.XX". On equity-fetch failure the readout shows "N shares" only.
**Why human:** Static analysis confirms the delegated `input` listener and `htmx:afterSettle` hook are wired in `edit-size-slider.js` with no blocked inline `oninput`. The drag-to-readout interaction requires a live browser to confirm the JS executes after HTMX injection.

#### 5. Dashboard approve-after-edit during market hours (carried forward)

**Test:** With the ASGI stack running and a paper Alpaca account configured, open `/approvals`, trigger the edit-size modal, adjust the slider to a valid in-cap size, and click "Approve at this size".
**Expected:** POST /approvals/{id}/edit-submit succeeds; proposal transitions APPROVED then EXECUTING then FILLED; audit log contains `edit_size` event with old_qty/new_qty, then `order_submitted` and `fill` events.
**Why human:** Requires live executor, open market hours, and a configured Alpaca paper account.

#### 6. Quiet-hours queuing behavior over time (time-gated, deferred from prior rounds)

**Test:** Configure quiet hours on a strategy, wait for the window, verify DMs arrive at window-open. Confirm safety-critical DMs (kill, executor errors, first-live fills) still fire during quiet hours.
**Expected:** No routine DMs during quiet window; DMs deferred to window-open; pager-channel categories bypass quiet hours.
**Why human:** Real-time behavior over 2+ hours cannot be verified statically.

#### 7. Daily P&L digest at 16:30 ET on a NYSE trading day (time-gated, deferred from prior rounds)

**Test:** On a NYSE trading day after 16:30 ET, verify the Block Kit P&L DM arrives with correct gross P&L and per-strategy breakdown.
**Expected:** Gross P&L computed correctly (BUYs subtract, SELLs add), no `_unknown_` buckets, no sign-flipped SELLs.
**Why human:** Requires real fill events from a live or paper trading session.

---

### Gaps Summary

No gaps found. All 6 must-haves for Plan 03-15 are verified in source. The three bugs (A, B, C) are fixed at the correct code locations with the specified patterns. The test file covers all 8 required cases without mocking the claim_action/transition_status/append_event primitives. Safety invariants (`_check_edit_size_caps`, OrderGuard, dedup UNIQUE constraint, dual-channel first-live gate) are untouched.

Status is `human_needed` because the three browser-UAT retests for the 03-15 bugs (items 1-3 above) and the four pre-existing time-gated/live-browser items (items 4-7) require a running ASGI stack or real market-hours conditions. Automated checks passed.

---

_Verified: 2026-06-23T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
_Scope: Plan 03-15 gap-closure re-verification (3-bug cluster: Bug A deeplink styling, Bug B duplicate session, Bug C terminal-state guard)_
